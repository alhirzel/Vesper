"""
Constants and functions pertaining to NFC bounding interval annotator datasets.
"""


import math

from tensorflow.data import Dataset, TFRecordDataset
from tensorflow.io import FixedLenFeature
import numpy as np
import tensorflow as tf

import vesper.util.signal_utils as signal_utils
import vesper.util.time_frequency_analysis_utils as tfa_utils


'''
Source datasets are tfrecord files.

Each source dataset is repeated, and elements from the different sources
are interleaved and parsed. Each element includes a waveform, clip start
and end indices, call start and end indices (when the element is a call),
and a clip ID.

Subsequent optional processing:

* Time reversal augmentation.

  The time reversal augmentation time reverses an example with
  probability .5.

* Slicing with optional time shift augmentation.

  The time shift augmentation randomly adjusts the slice bounds to
  distribute the call start time uniformly within some window.

* Amplitude scaling augmentation.

  The amplitude scaling augmentation randomly scales the waveform
  to distribute the log of its RMS amplitude uniformly within some
  window.
'''


'''
* Spectrogram units should be proportional to spectral density,
  or watts per hertz.
  
* The expected value of a spectrogram value for a white noise
  signal should not change with sample rate, window size, hop size,
  or DFT size.
  
* Changing the DFT size but not the window size is a kind of
  interpolation that should leave the spectrogram magnitude
  more or less unchanged.
  
* Sinusoidal signal power should scale with the window duration
  (measured in seconds), since the window bandwidth is inversely
  proportional to the duration. For example, for a bin-centered
  sinusoid, the value of its bin will double if the window
  duration doubles, since the same amount of signal power is
  present in a bin that is only half as wide.
  
* Should we scale spectrogram values somehow according to window
  type, i.e. as a function of the window coefficients?
  
* Decibel-unit-valued spectrograms should have a maximum value
  (perhaps, say, for white uniformly distributed noise of maximum
  amplitude) of around 100.
  
* The sample value range of waveforms from which spectrograms are
  computed should be [-1, 1]. Signals of different bit depths
  should be scaled to this common range before computing their
  spectrograms.
'''


# dataset parts
DATASET_PART_TRAINING = 'Training'
DATASET_PART_VALIDATION = 'Validation'
DATASET_PART_TEST = 'Test'

# dataset modes, which influence the preprocessing performed by the dataset
DATASET_MODE_TRAINING = 'Training'
DATASET_MODE_EVALUATION = 'Evaluation'
DATASET_MODE_INFERENCE = 'Inference'


_WAVEFORM_EXAMPLE_FEATURES = {
    'waveform': FixedLenFeature((), tf.string),
    'clip_start_index': FixedLenFeature((), tf.int64),
    'clip_end_index': FixedLenFeature((), tf.int64),
    'call_start_index': FixedLenFeature((), tf.int64),
    'call_end_index': FixedLenFeature((), tf.int64),
    'clip_id': FixedLenFeature((), tf.int64),
}


def create_waveform_dataset_from_tensors(waveforms):
    
    # One might like to just say:
    #
    #     dataset = tf.data.Dataset.from_tensor_slices(waveforms)
    #
    # here instead of using a generator, but that only works if
    # the waveforms all have the same length. Using a generator
    # works even if the waveform lengths differ.
    
    def generator():
        for waveform in waveforms:
            yield _normalize_waveform(waveform)
            
    return tf.data.Dataset.from_generator(generator, tf.float32)
    
    
def create_waveform_dataset_from_tfrecord_files(dir_path):
    
    """
    Creates a dataset of waveforms and associated metadata.
    
    Each dataset example has the form:
    
        (waveform, clip_start_index, clip_end_index, call_start_index,
         call_end_index, clip_id)
         
    All of the waveforms of the dataset have the same length. Each
    waveform contains one Vesper clip, which starts and ends at 
    waveform indices `clip_start_index` and `clip_end_index`. Each
    clip contains a nocturnal flight call that starts and ends at
    waveform indices `call_start_index` and `call_end_index`.
    
    The `clip_id` of a dataset example is the ID of the clip included
    in the waveform in the Vesper archive to which the clip belongs.
    """
    
    
    file_paths = dir_path.glob('*.tfrecords')
    
    # Convert tfrecord file paths from `Path` objects to strings.
    file_paths = sorted(str(p) for p in file_paths)
    
    # Shuffle file paths.
    file_paths = np.random.permutation(file_paths)
    
    # Create dataset of file paths.
    dataset = Dataset.from_tensor_slices(file_paths)
    
    # Create dataset of example protos, interleaving protos from the
    # different tfrecord files.
    dataset = dataset.interleave(
        _create_repeating_tfrecords_dataset,
        cycle_length=len(file_paths),
        num_parallel_calls=tf.data.experimental.AUTOTUNE)
    
    # Parse example protos.
    dataset = dataset.map(
        _parse_example,
        num_parallel_calls=tf.data.experimental.AUTOTUNE)
    
    return dataset
    
    
def _create_repeating_tfrecords_dataset(file_path):
    return TFRecordDataset([file_path]).repeat()
    
    
def _parse_example(proto):
    
    example = tf.io.parse_single_example(proto, _WAVEFORM_EXAMPLE_FEATURES)
    
    # Get waveform tensor.
    bytes_ = example['waveform']
    waveform = tf.io.decode_raw(bytes_, out_type=tf.int16, little_endian=True)
    waveform = _normalize_waveform(waveform)
    
    clip_start_index = example['clip_start_index']
    clip_end_index = example['clip_end_index']
    call_start_index = example['call_start_index']
    call_end_index = example['call_end_index']
    clip_id = example['clip_id']
    
    return (
        waveform, clip_start_index, clip_end_index,
        call_start_index, call_end_index, clip_id)
    
    
def _normalize_waveform(waveform):
    
    """
    Normalizes a waveform so it has 32-bit floating point samples in [-1, 1].
    """

    return tf.cast(waveform, tf.float32) / 32768


def create_training_dataset(dir_path, settings):
    
    """
    Creates a dataset suitable for training a neural network.
    
    Each dataset example has the form:
    
        (spectrogram slice, label)
        
    All of the spectrogram slices of the dataset have the same shape,
    of the form (spectrum count, bin count, 1). The exact shape depends
    on the values of several `settings` attributes. The spectrogram slices
    are suitable for input into a Keras convolutional neural network.
    
    The `label` of a dataset example is zero if the spectrogram slice
    does not contain a call starting at a certain index (it may or may
    not contain a call starting at another index). and one if it does
    contain a call starting at that index.
    """
    
    
    dataset = create_waveform_dataset_from_tfrecord_files(dir_path)
    
    processor = _ExampleProcessor(DATASET_MODE_TRAINING, settings)
    
    dataset = dataset.map(
        processor.preprocess_waveform,
        num_parallel_calls=tf.data.experimental.AUTOTUNE)
    
    dataset = dataset.map(
        processor.compute_spectrogram,
        num_parallel_calls=tf.data.experimental.AUTOTUNE)
    
    dataset = dataset.map(
        _diddle_example,
        num_parallel_calls=tf.data.experimental.AUTOTUNE)
    
    return dataset
    
    
def _diddle_example(gram, label, _):
    
    # Reshape gram for input into Keras CNN.
    gram = tf.expand_dims(gram, 2)
        
    # Return only gram and label, discarding clip ID.
    return gram, label


def create_validation_dataset(dir_path, settings):
    
    dataset = create_waveform_dataset_from_tfrecord_files(dir_path)
    
    dataset = dataset.map(
        _extract_clip_waveform,
        num_parallel_calls=tf.data.experimental.AUTOTUNE)
    
    return dataset


def _extract_clip_waveform(
        waveform, clip_start_index, clip_end_index, call_start_index,
        call_end_index, _):
    
    waveform = waveform[clip_start_index:clip_end_index]
    call_start_index -= clip_start_index
    call_end_index -= clip_start_index
    
    return waveform, call_start_index, call_end_index
    
    
def create_inference_dataset(waveform_dataset, settings):
    
    """
    Creates a dataset of spectrogram slice sequences.
    
    Each dataset example is a sequence of consecutive slices of the
    spectrogram of one input dataset waveform, with a hop size of one
    spectrum. The slices all have the same shape. Different dataset
    examples may have different numbers of slices, according to the
    (possibly differing) lengths of the input waveforms.
    """
    
    
    processor = _ExampleProcessor(DATASET_MODE_INFERENCE, settings)
    
    dataset = waveform_dataset.map(
        processor.compute_spectrogram,
        num_parallel_calls=tf.data.experimental.AUTOTUNE)
    
    dataset = dataset.map(
        processor.slice_spectrogram,
        num_parallel_calls=tf.data.experimental.AUTOTUNE)
    
    return dataset


class _ExampleProcessor:
     
    """
    Dataset example processor.
     
    A dataset example processor prepares dataset examples for input to
    a neural network during training or inference. It performs waveform
    slicing, waveform modifications for dataset augmentation, and
    spectrogram computation.
    """
     
     
    def __init__(self, mode, settings):
         
        # `mode` can be `DATASET_MODE_TRAINING` or `DATASET_MODE_INFERENCE`.
        #
        # When `mode` is `DATASET_MODE_TRAINING`, dataset examples are
        # preprocessed according to certain settings that control waveform
        # slicing and modification.
        #
        # When `mode` is `DATASET_MODE_INFERENCE`, waveform slicing
        # and modification are disabled.
 
        self._settings = settings
         
        s = settings
        
        data_augmentation_enabled = \
            mode == DATASET_MODE_TRAINING or \
            (mode == DATASET_MODE_EVALUATION and
                s.evaluation_data_augmentation_enabled)
                
        self._waveform_time_reversal_data_augmentation_enabled = \
            data_augmentation_enabled and \
            s.waveform_time_reversal_data_augmentation_enabled
        
        self._call_start_time_shift_data_augmentation_enabled = \
            data_augmentation_enabled
            
        s2f = signal_utils.seconds_to_frames
        sample_rate = s.waveform_sample_rate
        self._waveform_slice_length = \
            s2f(s.waveform_slice_duration, sample_rate)
        if self._call_start_time_shift_data_augmentation_enabled:
            call_start_window_start_time = \
                s.call_start_window_center_time - \
                s.call_start_window_duration / 2
            self._positive_slice_window_start_offset = \
                -s2f(call_start_window_start_time, s.waveform_sample_rate)
            self._call_start_window_length = \
                s2f(s.call_start_window_duration, s.waveform_sample_rate)
                
        self._waveform_amplitude_scaling_data_augmentation_enabled = \
            data_augmentation_enabled and \
            s.waveform_amplitude_scaling_data_augmentation_enabled
            
        (self._window_size, self._hop_size, self._dft_size,
         self._freq_start_index, self._freq_end_index) = \
            _get_low_level_spectrogram_settings(s)
        
        self._window_fn = tf.signal.hann_window
        
        
    def preprocess_waveform(
            self, waveform, clip_start_index, clip_end_index,
            call_start_index, call_end_index, clip_id):
        
        """
        Preprocesses one input waveform.
        
        Slices and applies data augmentations to the specified waveform
        according to this preprocessor's settings.
        """
        
        if self._waveform_time_reversal_data_augmentation_enabled:
            waveform, call_start_index, call_end_index = \
                _maybe_time_reverse_waveform(
                    waveform, call_start_index, call_end_index)
        
        waveform_slice, slice_start_index, label = \
            self._slice_waveform(waveform, call_start_index)
        
        # Cast waveform to float32 for subsequent processing.
        waveform_slice = tf.cast(waveform_slice, tf.float32)
        
        if self._waveform_amplitude_scaling_data_augmentation_enabled:
            waveform_slice = self._scale_waveform_amplitude(waveform_slice)
        
        return (waveform_slice, label, clip_id)
        
        
    def _slice_waveform(self, waveform, call_start_index):
        
        # Get start and end indices of window in which slice for positive
        # example should start.
        positive_slice_window_start_index = \
            call_start_index + self._positive_slice_window_start_offset
        positive_slice_window_end_index = \
            positive_slice_window_start_index + self._call_start_window_length
            
        # Decide whether example is positive or negative.
        positive = tf.random.uniform(()) <= self._settings.positive_probability
        
        if positive:
            
            # Slice so call start is within call start window.
            minval = positive_slice_window_start_index
            maxval = positive_slice_window_end_index
            slice_start_index = \
                tf.random.uniform((), minval, maxval, dtype=tf.int64)
            
        else:
            # negative example
            
            # Slice so call start is outside of call start window.
            minval = 0
            maxval = tf.cast(
                len(waveform) - self._waveform_slice_length -
                self._call_start_window_length, tf.int64)
            slice_start_index = \
                tf.random.uniform((), minval, maxval, dtype=tf.int64)
            if slice_start_index >= positive_slice_window_start_index:
                slice_start_index += self._call_start_window_length
                
        slice_end_index = slice_start_index + self._waveform_slice_length
        waveform_slice = waveform[slice_start_index:slice_end_index]
        
        label = 1 if positive else 0
        
        return waveform_slice, slice_start_index, label
    
    
    def _scale_waveform_amplitude(self, waveform):
        
        max_abs = tf.math.reduce_max(tf.math.abs(waveform))

        if max_abs == 0:
            # waveform samples are all zero
            
            return waveform
        
        else:
            # waveform samples are not all zero
            
            # Find scale factor that would make maximum absolute waveform
            # value one.
            max_factor = _f32(1) / max_abs
            
            # Find scale factor that would reduce RMS waveform value to
            # 1 / 256. Yield 1 if RMS value is already less than 1 / 256.
            sum_squared = tf.math.reduce_sum(waveform * waveform)
            size = tf.cast(tf.size(waveform), tf.float32)
            rms = tf.math.sqrt(sum_squared / size)
            min_factor = tf.math.minimum(_f32(1), _f32(1 / 256) / rms)
            
            # Choose random factor between `min_factor` and `max_factor`,
            # with distribution uniform on log scale.
            max_log = tf.math.log(max_factor)
            min_log = tf.math.log(min_factor)
            log_factor = tf.random.uniform(
                (), min_log, max_log, dtype=tf.float32)
            factor = tf.math.exp(log_factor)
            
            # Scale waveform by chosen factor.
            return factor * waveform
            
            
    def compute_spectrogram(self, waveform, *args):

        """Computes the spectrogram of a waveform."""
        
        s = self._settings
        
        # Compute STFT. To use `tf.signal.stft`, we must add a leading
        # unit dimension to the waveform tensor. After the call to
        # `tf.signal.stft` we effectively remove the corresponding
        # dimension of the resulting `stfts` tensor.
        waveforms = tf.expand_dims(waveform, 0)
        stfts = tf.signal.stft(
            waveforms, self._window_size, self._hop_size, self._dft_size,
            self._window_fn)
        stft = stfts[0]
        
        # Slice STFT along frequency axis.
        stft = stft[..., self._freq_start_index:self._freq_end_index]
        
        # Get spectrogram, i.e. squared magnitude of STFT.
        gram = tf.math.real(stft * tf.math.conj(stft))
        # gram = tf.abs(stft) ** 2
        
        # Normalize spectrogram values so a full-scale, bin-centered
        # sinusoid has a value of one with a rectangular window.
        # TODO: Consider using a different normalization scheme that
        # yields more consistent values (proportional to the spectral
        # density, with units of watts per hertz) for noise across
        # different sample rates, window sizes, and DFT sizes. This
        # is what we'd like to use for spectrogram display, and it
        # seems that we might as well use it here, too. It isn't
        # necessary to build a working system, but the consistency
        # might be helpful, for example for dataset visualization. 
        normalizing_scale_factor = 1 / (self._window_size / 2) ** 2
        gram *= normalizing_scale_factor
        
        # Take spectrogram log and apply affine transform to put
        # full scale sinusoids at about 100 dB.
        gram = tf.math.log(gram + s.spectrogram_log_epsilon)
        decibel_scale_factor = 10 / math.log(10)
        gram = 100 + decibel_scale_factor * gram
                
        return (gram,) + tuple(args)
    
    
    def adjust_call_bounds_for_spectrogram(
            self, gram, call_start_index, call_end_index, clip_id):
        
        call_start_index /= self._hop_size
        call_end_index /= self._hop_size
        
        return gram, call_start_index, call_end_index, clip_id
    
    
    def slice_spectrogram(self, gram, *args):
    
        s = self._settings
        slice_duration = s.waveform_slice_duration
        window_size = s.spectrogram_window_size
        hop_size = window_size * s.spectrogram_hop_size / 100
        slice_length = \
            1 + int(round((slice_duration - window_size) / hop_size))
        
        forward_slices = _slice_spectrogram(gram, slice_length, 1)
        
        reversed_gram = tf.reverse(gram, axis=(0,))
        backward_slices = _slice_spectrogram(reversed_gram, slice_length, 1)
        
        return (forward_slices, backward_slices) + tuple(args)


def _get_low_level_spectrogram_settings(settings):
     
    s = settings
    fs = s.waveform_sample_rate
    s2f = signal_utils.seconds_to_frames
     
    # spectrogram
    window_size = s2f(s.spectrogram_window_size, fs)
    fraction = s.spectrogram_hop_size / 100
    hop_size = s2f(s.spectrogram_window_size * fraction, fs)
    dft_size = tfa_utils.get_dft_size(window_size)
     
    # frequency slicing
    f2i = tfa_utils.get_dft_bin_num
    freq_start_index = f2i(s.spectrogram_start_freq, fs, dft_size)
    freq_end_index = f2i(s.spectrogram_end_freq, fs, dft_size) + 1
     
    return (window_size, hop_size, dft_size, freq_start_index, freq_end_index)


def _maybe_time_reverse_waveform(waveform, call_start_index, call_end_index):
     
    reverse = tf.random.uniform((), maxval=2, dtype=tf.int32)
      
    if reverse != 0:
     
        # Reverse waveform.
        waveform = tf.reverse(waveform, [0])
         
        # Get waveform length, casting to int64 for arithmetic below.
        length = tf.cast(len(waveform), tf.int64)
         
        # Swap and complement call bounds.
        temp = call_start_index
        call_start_index = length - call_end_index
        call_end_index = length - temp
         
    return waveform, call_start_index, call_end_index
 
 
def _f32(x):
    return tf.cast(x, tf.float32)


def _slice_spectrogram(gram, slice_length, slice_step):
    
    # Get tensor of consecutive spectrogram slices.
    slices = tf.signal.frame(gram, slice_length, slice_step, axis=0)
    
    # Add trailing dimension for input into Keras CNN.
    slices = tf.expand_dims(slices, 3)
    
    return slices
    
    
def _main():
    _test_stft()


def _test_stft():
    
    sample_rate = 24000
    epsilon = 1e-10
    
    for window_size in (8, 12, 16, 20, 24, 28, 32, 48, 64):
        
        waveform = _create_sinusoid(window_size, sample_rate)
        
        waveforms = tf.expand_dims(waveform, 0)
        
        dft_size = tfa_utils.get_dft_size(window_size)
        
        stft = tf.signal.stft(
            waveforms, window_size, window_size, dft_size, None)
        
        gram = tf.abs(stft) ** 2
        
        normalizing_scale_factor = 1 / (window_size / 2) ** 2
        gram *= normalizing_scale_factor
         
        decibel_scale_factor = 10 / math.log(10)
        gram = 100 + decibel_scale_factor * tf.math.log(gram + epsilon)
        
        print(window_size, gram)


def _create_sinusoid(window_size, sample_rate):
    freq = 3000
    phase_factor = 2 * math.pi * freq / sample_rate
    phases = phase_factor * tf.range(window_size, dtype=tf.float32)
    return tf.math.cos(phases)
        
        
def _test_stft_new():
    
    epsilon = 1e-10
    bin_num = 1
    trial_count = 1000
    
    for sample_rate in (22050, 24000, 32000, 41000, 48000):
        
        for window_dur in (.005, .010, .015):
            
            bin_value_sum = 0
            
            for trial_num in range(trial_count):
            
                window_size = int(round(window_dur * sample_rate))
                
                # waveform = _create_sinusoid(window_size, sample_rate)
                waveform = _create_white_noise(window_size)
                
                waveforms = tf.expand_dims(waveform, 0)
                
                dft_size = tfa_utils.get_dft_size(window_size) * 4
                
                # window_fn = tf.signal.hann_window
                window_fn = None
                stft = tf.signal.stft(
                    waveforms, window_size, window_size, dft_size, window_fn)
                
                gram = tf.abs(stft) ** 2
                
                bin_value_sum += gram[0, 0, bin_num]
                
#                 normalizing_scale_factor = 1 / (window_size / 2) ** 2
#                 gram *= normalizing_scale_factor
#                    
#                 decibel_scale_factor = 10 / math.log(10)
#                 gram = 100 + decibel_scale_factor * tf.math.log(gram + epsilon)
            
            bin_value_avg = bin_value_sum / trial_count
            print(
                sample_rate, window_dur, window_size, dft_size,
                bin_value_avg.numpy())
            
        
def _create_white_noise(window_size):
    return tf.random.uniform((window_size,), minval=-1, maxval=1)
    
    
if __name__ == '__main__':
    _main()
