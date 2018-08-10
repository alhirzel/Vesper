"""Module containing class `DetectCommand`."""


from collections import defaultdict
import datetime
import itertools
import logging
import random
import time

from django.db import transaction

from vesper.command.command import Command, CommandExecutionError
from vesper.django.app.models import (
    Clip, Job, Processor, Recording, RecordingChannel, Station)
from vesper.old_bird.old_bird_detector_runner import OldBirdDetectorRunner
from vesper.signal.wave_audio_file import WaveAudioFileReader
from vesper.singletons import clip_manager, extension_manager, preset_manager
from vesper.util.schedule import Interval, Schedule
import vesper.command.command_utils as command_utils
import vesper.django.app.model_utils as model_utils
import vesper.util.archive_lock as archive_lock
import vesper.util.signal_utils as signal_utils
import vesper.util.text_utils as text_utils
import vesper.util.time_utils as time_utils
from vesper.django.app.views import clip


_RUN_DETECTORS = True
"""
`True` for normal operation, or `False` for command to do everything it
normally does but not actually run detectors.
"""


_ARCHIVE_CLIPS = True
"""
`True` for normal operation, or `False` for command to not add detected
clips to archive. Note that if `_RUN_DETECTORS` is `False` the value of
this attribute is irrelevant.
"""


_DETECTION_CHUNK_SIZE = 100000
"""Detection chunk size in sample frames."""


_CLIP_BATCH_SIZE = 10
"""
Number of clips to write to archive in a single database transaction.

The following table shows statistics from detector runs on the same
recording with various batch sizes. (The recording was made on the
night of 2017-09-29 at the MPG Ranch Floodplain station, the detector
was the PNF Tseep Energy Detector 1.0, and the detector was run on the
portion of the recording from an hour after sunset to a half hour before
runrise on a 2012 MacBook Pro.) The "Duration" column shows the mean
database transaction duration, while the "Speed" column shows the speed
of the detector run in number of times faster than real time.

    Batch Size      Duration (ms)      Speed (xrt)
    ----------      -------------      -----------
        1                3.1              782
        10               9                1020
        100              54               1068
        1000             462              1088
        10000            2483             1063
        
A batch size of 10 provides both a reasonably short transaction duration,
which is important for concurrency support, and fast detection.
"""


_PROCESS_RANDOM_STATION_NIGHTS = False
"""
`True` if command should run detectors on only a random subset of the
station-nights specified by the command arguments. The subset is
specified by `_START_STATION_NIGHT_INDEX` and `_END_STATION_NIGHT_INDEX`.
"""


_START_STATION_NIGHT_INDEX = 0
"""
Start index of shuffled station-nights for which to run detectors, when
`_PROCESS_RANDOM_STATION_NIGHTS` is `True`.
"""


_END_STATION_NIGHT_INDEX = 10
"""
End index of shuffled station-nights for which to run detectors, when
`_PROCESS_RANDOM_STATION_NIGHTS` is `True`.
"""


class DetectCommand(Command):
    
    
    extension_name = 'detect'
    
    
    def __init__(self, args):
        
        super().__init__(args)
        
        get = command_utils.get_required_arg
        self._detector_names = get('detectors', args)
        self._station_names = get('stations', args)
        self._start_date = get('start_date', args)
        self._end_date = get('end_date', args)
        self._schedule_name = get('schedule', args)
        self._create_clip_files = get('create_clip_files', args)
        
        self._schedule = _get_schedule(self._schedule_name)
        self._station_schedules = {}
                
        self._process_random_station_nights = _PROCESS_RANDOM_STATION_NIGHTS
        self._start_station_night_index = _START_STATION_NIGHT_INDEX
        self._end_station_night_index = _END_STATION_NIGHT_INDEX
        
        
    def execute(self, job_info):
        
        self._job_info = job_info
        self._logger = logging.getLogger()

        detectors = self._get_detectors()
        old_bird_detectors, other_detectors = _partition_detectors(detectors)
        
        recording_lists = self._get_recording_lists()
        station_nights = sorted(recording_lists.keys())
        
        for i, station_night in enumerate(station_nights):
            
            self._log_station_night(station_night, i, len(station_nights))
            
            recordings = recording_lists[station_night]
            self._run_old_bird_detectors(old_bird_detectors, recordings)
            self._run_other_detectors(other_detectors, recordings)
            
        return True
    
    
    def _get_detectors(self):
        
        try:
            return [self._get_detector(name) for name in self._detector_names]
        
        except Exception as e:
            self._logger.error((
                'Collection of detectors to run on recordings on failed with '
                'an exception.\n'
                'The exception message was:\n'
                '    {}\n'
                'The archive was not modified.\n'
                'See below for exception traceback.').format(str(e)))
            raise
            
            
    def _get_detector(self, name):
        try:
            return model_utils.get_processor(name, 'Detector')
        except Processor.DoesNotExist:
            raise CommandExecutionError(
                'Unrecognized detector "{}".'.format(name))
            
            
    def _get_recording_lists(self):
        
        try:
            
            # Get iterator for all recordings of specified station-nights.
            recordings = itertools.chain.from_iterable(
                self._get_station_recordings(
                    name, self._start_date, self._end_date)
                for name in self._station_names)
            
            # Get mapping from station-nights to recording lists.
            recording_lists = defaultdict(list)
            for recording in recordings:
                station = recording.station
                night = station.get_night(recording.start_time)
                recording_lists[(station.name, night)].append(recording)
            
            total_num_station_nights = len(recording_lists)
            station_nights_text = text_utils.create_count_text(
                total_num_station_nights, 'station-night')
            
            if self._process_random_station_nights:
                # will process recordings for randomly selected subset
                # of station-nights
                
                recording_lists = self._select_recording_lists(recording_lists)
                
                start_num = self._start_station_night_index + 1
                end_num = self._end_station_night_index
                
                self._logger.info((
                    'This command will process recordings for '
                    'station-nights {} to {} of a shuffled sequence '
                    'of {}.').format(start_num, end_num, station_nights_text))
                
            else:
                # will process recordings for all station-nights
                
                self._logger.info(
                    'This command will process recordings for {}.'.format(
                        station_nights_text))
                    
            # Sort recordings for each station-night by start time.
            for recordings in recording_lists.values():
                recordings.sort(key=lambda r: r.start_time)

            return recording_lists
        
        except Exception as e:
            self._logger.error((
                'Collection of recordings to process failed with '
                'an exception.\n'
                'The exception message was:\n'
                '    {}\n'
                'The archive was not modified.\n'
                'See below for exception traceback.').format(str(e)))
            raise

            
    def _get_station_recordings(self, station_name, start_date, end_date):

        # TODO: Test behavior for an unrecognized station name.
        # I tried this on 2016-08-23 and got results that did not
        # make sense to me. An exception was raised, but it appeared
        # to be  raised from within code that followed the except clause
        # in the `execute` method above (the code logged the sequence of
        # recordings returned by the `_get_recordings` method) rather
        # than from within that clause, and the error message that I
        # expected to be logged by that clause did not appear in the log.
        
        try:
            station = Station.objects.get(name=station_name)
        except Station.DoesNotExist:
            raise CommandExecutionError(
                'Unrecognized station "{}".'.format(station_name))
        
        time_interval = station.get_night_interval_utc(start_date, end_date)
        
        return Recording.objects.filter(
            station=station,
            start_time__range=time_interval)


    def _select_recording_lists(self, recording_lists):
        
        # self._show_station_nights('all station-nights', recording_lists)
            
        # Get all station-nights, sorting to ensure reproducibility
        # of shuffling (below) across detection runs.
        station_nights = sorted(recording_lists.keys())
        
        # Shuffle station-nights. Always seed random number generator
        # to ensure reproducibility across detection runs.
        random.seed(0)
        random.shuffle(station_nights)
        
        # Get station-nights for which to run detectors.
        start_index = self._start_station_night_index
        end_index = self._end_station_night_index
        station_nights = sorted(station_nights[start_index:end_index])
        
        # Get recording lists for selected station-nights.
        recording_lists = dict(
            (sn, recording_lists[sn]) for sn in station_nights)
        
        # self._show_station_nights('selected station-nights', recording_lists)
        
        return recording_lists
            

    def _show_station_nights(self, header, recording_lists):
        station_nights = sorted(recording_lists.keys())
        print('{}:'.format(header))
        for i, sn in enumerate(station_nights):
            station_name, night = sn
            print(i, station_name, str(night), len(recording_lists[sn]))
        print(len(station_nights))
        

    def _log_station_night(self, station_night, i, n):
        
        station_name, night = station_night
        self._logger.info((
            'Processing recordings for station-night {} of {} - '
            '"{} {}"...').format(i + 1, n, station_name, str(night)))


    def _run_old_bird_detectors(self, detectors, recordings):
        
        if len(detectors) == 0:
            return
        
        for recording in recordings:
            
            recording_files = recording.files.all()
            
            if len(recording_files) == 0:
                self._logger.info(
                    'No file information available for recording "{}".'.format(
                        str(recording)))
                
            else:
                num_channels = recording.num_channels
                for file_ in recording_files:
                    for channel_num in range(num_channels):
                        runner = OldBirdDetectorRunner(self._job_info)
                        runner.run_detectors(detectors, file_, channel_num)

        
    def _run_other_detectors(self, detector_models, recordings):
        
        if len(detector_models) == 0:
            return
        
        num_recordings = len(recordings)
        
        for i, recording in enumerate(recordings):
            
            self._logger.info(
                '    Processing recording {} of {} - "{}"...'.format(
                    i + 1, num_recordings, str(recording)))
            
            recording_files = recording.files.all()
            
            if len(recording_files) == 0:
                self._logger.error(
                    '        Archive has no file information for this '
                    'recording, so no detectors will be run on it.')
                
            else:
                
                recording_intervals = self._get_detection_intervals(recording)
            
                for file_ in recording_files:
                    self._run_other_detectors_on_file(
                        detector_models, file_, recording_intervals)
                    
                    
    def _get_detection_intervals(self, recording):
                    
        schedule = self._get_detection_schedule(recording.station)
        start_time = recording.start_time
        end_time = recording.end_time
        
        if schedule is None:
            return [Interval(start=start_time, end=end_time)]
        
        else:
            # have schedule
            
            schedule_intervals = schedule.get_intervals(start_time, end_time)
            
            recording_interval = Interval(start=start_time, end=end_time)
            
            # Here we know from the contract of `Schedule.get_intervals`
            # that each interval of `schedule_intervals` intersects the
            # recording interval, so we don't have to worry about
            # `_get_time_intervals_intersection` returning `None`.
            return [
                _get_time_intervals_intersection(i, recording_interval)
                for i in schedule_intervals]
        
    
    def _get_detection_schedule(self, station):
        
        if self._schedule is None:
            return None
        
        else:
            # have schedule
            
            try:
                return self._station_schedules[station.name]
            
            except KeyError:
                # schedule cache miss
                
                # Create schedule for this station.
                schedule = Schedule.compile_dict(
                    self._schedule, station.latitude, station.longitude,
                    station.time_zone)
                
                # Add schedule to cache.
                self._station_schedules[station.name] = schedule
                
                return schedule
        
            
    def _run_other_detectors_on_file(
            self, detector_models, file_, recording_intervals):
                
        if file_.path is None:
            
            self._logger.error((
                '        Archive has no path for file {} of recording, '
                'so no detectors will be run on it.').format(file_.num))
        
        else:
            
            try:
                abs_path = model_utils.get_absolute_recording_file_path(file_)
                
            except ValueError as e:
                self._logger.error('        ' + str(e))
                
            else:
                # have absolute path of recording file
                
                reader = WaveAudioFileReader(str(abs_path))
                
                intervals = _get_file_detection_intervals(
                    file_, recording_intervals)
                
                if len(intervals) == 0:
                    self._logger.info((
                        '        There are no detection intervals '
                        'in file "{}", so no detectors will be run on '
                        'it.').format(abs_path))
                    
                for interval in intervals:
                    self._run_other_detectors_on_file_interval(
                        detector_models, file_, abs_path, reader, interval)
                    
                    
    def _run_other_detectors_on_file_interval(
            self, detector_models, file_, file_path, file_reader,
            time_interval):
        
        # Log detection start message.
        self._log_detection_start(
            detector_models, file_path, file_, time_interval)
                
        start_time = time.time()
        
        if _RUN_DETECTORS:
            
            # Convert time interval to index interval.
            index_interval = _get_index_interval(
                time_interval, file_.start_time, file_.sample_rate)
                 
            # Create detectors.
            detectors = self._create_detectors(
                detector_models, file_.recording, file_reader,
                file_.start_index, index_interval.start)
                  
            # Detect.
            for samples in _generate_sample_buffers(
                    file_reader, index_interval):
                for detector in detectors:
                    channel_samples = samples[detector.channel_num]
                    detector.detect(channel_samples)
                      
            # Wrap up detection.
            for detector in detectors:
                detector.complete_detection()
                
        else:
            # don't run detectors
            
            time.sleep(.1)

        processing_time = time.time() - start_time
        
        # Log detection performance message.
        interval_duration = \
            (time_interval.end - time_interval.start).total_seconds()
        self._log_detection_performance(
            len(detector_models), file_.num_channels, interval_duration,
            processing_time)
                    
                
    def _log_detection_start(
            self, detector_models, file_path, file_, time_interval):
         
        if time_interval.start == file_.start_time and \
                time_interval.end == file_.end_time:
            # running detectors on entire file
             
            interval_text = ''
             
        else:
            # not running detectors on entire file
            
            start = _format_datetime(time_interval.start)
            end = _format_datetime(time_interval.end)
            
            if time_interval.start == file_.start_time:
                # interval includes file start
                
                interval_text = ' interval [file start, {}]'.format(end)
                
            elif time_interval.end == file_.end_time:
                # interval includes file end
                
                interval_text = ' interval [{}, file end]'.format(start)
                
            else:
                # interval includes neither file start nor file end
                
                interval_text = ' interval [{}, {}]'.format(start, end)                 
             
        detectors_text = text_utils.create_units_text(
            len(detector_models), 'detector')
        
        self._logger.info(
            '        Running {} on file "{}"{}...'.format(
                detectors_text, file_path, interval_text))
        

    def _create_detectors(
            self, detector_models, recording, file_reader,
            file_start_index, interval_start_index):
        
        num_channels = recording.num_channels
        
        detectors = []
        
        job = Job.objects.get(id=self._job_info.job_id)

        for detector_model in detector_models:
            
            for channel_num in range(num_channels):
                
                recording_channel = RecordingChannel.objects.get(
                    recording=recording, channel_num=channel_num)
                
                listener = _DetectorListener(
                    detector_model, recording, recording_channel,
                    file_start_index, interval_start_index,
                    self._create_clip_files, file_reader, job, self._logger)
                
                detector = _create_detector(
                    detector_model, recording, listener)
                
                # We add a `channel_num` attribute to each detector to keep
                # track of which recording channel it is for.
                detector.channel_num = channel_num
                
                detectors.append(detector)
            
        return detectors


    def _log_detection_performance(
            self, num_detectors, num_channels, interval_duration,
            processing_time):
        
        format_ = text_utils.format_number
        
        dur = format_(interval_duration)
        time = format_(processing_time)
        
        detectors_text = text_utils.create_count_text(
            num_detectors, 'detector')

        message = (
            '        Ran {} on {} seconds of {}-channel '
            'audio in {} seconds').format(
                detectors_text, dur, num_channels, time)
        
        if processing_time != 0:
            total_duration = num_detectors * num_channels * interval_duration
            speedup = format_(total_duration / processing_time)
            message += ', {} times faster than real time.'.format(speedup)
        else:
            message += '.'
            
        self._logger.info(message)
        

def _get_schedule(schedule_name):
    
    if schedule_name == '':
        return None
    
    else:
        
        preset = preset_manager.instance.get_preset(
            'Detection Schedule', schedule_name)
        
        return preset.data
        

# This module must be able to distinguish between the original Old Bird
# Tseep and Thrush detectors and other detectors since there are special
# considerations for running the Old Bird detectors. We will probably
# eventually drop support for the original Old Bird detectors, at which
# point we can be rid of this ugliness.
_OLD_BIRD_DETECTOR_NAMES = (
    'Old Bird Thrush Detector',
    'Old Bird Tseep Detector'
)


def _partition_detectors(detectors):
    
    old_bird_detectors = []
    other_detectors = []
    
    for detector in detectors:
        
        if detector.name in _OLD_BIRD_DETECTOR_NAMES:
            old_bird_detectors.append(detector)
            
        else:
            other_detectors.append(detector)
            
    return (old_bird_detectors, other_detectors)


def _get_time_intervals_intersection(a, b):
    
    if a.end < b.start or b.end < a.start:
        # intervals do not intersect
        
        return None
    
    else:
        # intervals intersect
        
        start = a.start if b.start < a.start else b.start
        end = a.end if a.end < b.end else b.end
        return Interval(start=start, end=end)


def _get_file_detection_intervals(file_, recording_intervals):
    
    """Gets the audio file index intervals on which to run detectors."""
    
    file_interval = Interval(file_.start_time, file_.end_time)
    
    detection_intervals = [
        _get_time_intervals_intersection(i, file_interval)
        for i in recording_intervals]
    
    # Remove any `None` elements from detection intervals list.
    detection_intervals = [i for i in detection_intervals if i is not None]
    
    return detection_intervals
    

def _get_index_interval(time_interval, start_time, sample_rate):
    
    """
    Gets the audio file index interval corresponding to the specified
    time interval.
    """
    
    start_offset = (time_interval.start - start_time).total_seconds()
    start_index = signal_utils.seconds_to_frames(start_offset, sample_rate)
    
    duration = (time_interval.end - time_interval.start).total_seconds()
    length = signal_utils.seconds_to_frames(duration, sample_rate)
    
    return Interval(start=start_index, end=start_index + length)


def _get_index_intervals_intersection(a, b):
    
    if a.end <= b.start or b.end <= a.start:
        # intervals do not intersect
        
        return None
    
    else:
        # intervals intersect
        
        start = a.start if b.start < a.start else b.start
        end = a.end if a.end < b.end else b.end
        return Interval(start=start, end=end)
    
    
def _generate_sample_buffers(file_reader, interval):
    
    index = interval.start
    end_index = interval.end
    
    while index != end_index:
        length = min(_DETECTION_CHUNK_SIZE, end_index - index)
        yield file_reader.read(index, length)
        index += length
        
        
def _format_datetime(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S UTC')


# TODO: Who is the authority regarding detectors: `Processor` instances
# or the extension manager? Right now detector names are stored redundantly
# in both `Processor` instances and the extensions, and hence there is
# the potential for inconsistency. We populate UI controls from the
# `Processor` instances, but construct detectors using the extension
# manager, which finds extensions using the names stored in the extensions
# themselves. How might we eliminate the redundancy? Be sure to consider
# versioning and the possibility of processing parameters when thinking
# about this.
def _create_detector(detector_model, recording, listener):
    
    detector_name = detector_model.name
    
    classes = extension_manager.instance.get_extensions('Detector')
    
    try:
        cls = classes[detector_name]
    except KeyError:
        raise ValueError('Unrecognized detector "{}".'.format(detector_name))
    
    return cls(recording.sample_rate, listener)


class _DetectorListener:
    
    
    def __init__(
            self, detector_model, recording, recording_channel,
            file_start_index, interval_start_index, create_clip_files,
            file_reader, job, logger):
        
        self._detector_model = detector_model
        self._recording = recording
        self._recording_channel = recording_channel
        self._file_start_index = file_start_index          # index in recording
        self._interval_start_index = interval_start_index  # index in file
        self._create_clip_files = create_clip_files
        self._file_reader = file_reader
        self._job = job
        self._logger = logger
        
        self._clip_manager = clip_manager.instance
        self._clips = []
        self._num_clips = 0
        self._num_database_failures = 0
        self._num_file_failures = 0
        
#         self._num_transactions = 0
#         self._total_transactions_duration = 0
        
        
    def process_clip(self, start_index, length, threshold=None):
        
        self._clips.append((start_index, length))
        self._num_clips += 1
        
        if len(self._clips) == _CLIP_BATCH_SIZE:
            self._archive_clips(threshold)
        
        
    def _archive_clips(self, threshold):
        
        if _ARCHIVE_CLIPS:
            
            station = self._recording.station
            recording_channel = self._recording_channel
            mic_output = recording_channel.mic_output
            detector_model = self._detector_model
            
            start_offset = self._file_start_index + self._interval_start_index
            sample_rate = self._recording.sample_rate
             
            creation_time = time_utils.get_utc_now()
            
            create_clip_files = self._create_clip_files
            
            if create_clip_files:
                clips = []
             
            # Create database records for current batch of clips in one
            # database transaction.
            
#             trans_start_time = time.time()
            
            failure_info = None
            
            with archive_lock.atomic():
                
                with transaction.atomic():
                     
                    for start_index, length in self._clips:
                        
                        try:
                        
                            # Get clip start time as a `datetime`.
                            start_index += start_offset
                            start_delta = datetime.timedelta(
                                seconds=start_index / sample_rate)
                            start_time = \
                                self._recording.start_time + start_delta
                             
                            end_time = signal_utils.get_end_time(
                                start_time, length, sample_rate)
                         
                            # It would be nice to use Django's `bulk_create`
                            # here, but unfortunately that won't automatically
                            # set clip IDs for us except (as of this writing)
                            # if we're using PostgreSQL.
                            clip = Clip.objects.create(
                                station=station,
                                mic_output=mic_output,
                                recording_channel=recording_channel,
                                start_index=start_index,
                                length=length,
                                sample_rate=sample_rate,
                                start_time=start_time,
                                end_time=end_time,
                                date=station.get_night(start_time),
                                creation_time=creation_time,
                                creating_user=None,
                                creating_job=self._job,
                                creating_processor=detector_model
                            )
                            
                            if create_clip_files:
                                
                                # Save clip so we can create clip file
                                # outside of transaction.
                                clips.append(clip)
                        
                        except Exception as e:
                            failure_info = (start_time, length, str(e))
                            break

#             trans_end_time = time.time()
#             self._num_transactions += 1
#             self._total_transactions_duration += \
#                 trans_end_time - trans_start_time
            
            if failure_info is not None:
                # clip creation succeeded
                
                start_time, length, message = failure_info
                
                duration = signal_utils.get_duration(length, sample_rate)
                    
                clip_string = Clip.get_string(
                    station.name, mic_output.name, detector_model.name,
                    start_time, duration)
                
                batch_size = len(self._clips)
                self._num_database_failures += batch_size
                
                if batch_size == 1:
                    prefix = 'Clip'
                else:
                    prefix = 'All {} clips in this batch'.format(batch_size)
                    
                self._logger.error((
                    '            Attempt to create database record for '
                    'clip {} failed with message: {}. {} will be '
                    'ignored.').format(clip_string, message, prefix))

            elif create_clip_files:
                # clip creation succeeded
                
                for clip in clips:
                    
                    try:
                        self._clip_manager.create_audio_file(clip)
                        
                    except Exception as e:
                        self._num_file_failures += 1
                        self._logger.error((
                            '            Attempt to create audio file for '
                            'clip {} failed with message: {}. Clip database '
                            'record was still created.').format(
                                str(clip), str(e)))
                        
        self._clips = []
        
#         self._logger.info(
#             '        Processed {} clips from detector "{}"...'.format(
#                 self._num_clips, self._detector_model.name))


    def complete_processing(self, threshold=None):
        
        # Archive remaining clips.
        self._archive_clips(threshold)
            
        clips_text = text_utils.create_count_text(self._num_clips, 'clip')
        
        if self._num_database_failures == 0 and self._num_file_failures == 0:
            
            self._logger.info((
                '        Archived {} from detector "{}".').format(
                    clips_text, self._detector_model.name))
            
        else:
            
            db_failures_text = text_utils.create_count_text(
                self._num_database_failures,
                'database record creation failure')
            
            if self._create_clip_files:
                
                num_file_failures = \
                    self._num_database_failures + self._num_file_failures
                
                file_failures_text = ' and ' + text_utils.create_count_text(
                    num_file_failures, 'audio file creation failure')
                
            else:
                
                file_failures_text = ''
            
            self._logger.info(
                '        Processed {} from detector "{}" with {}{}.'.format(
                    clips_text, self._detector_model.name,
                    db_failures_text, file_failures_text))
        
#         avg = self._total_transactions_duration / self._num_transactions
#         self._logger.info((
#             '        Average database transaction duration was {} '
#             'seconds.').format(avg))
