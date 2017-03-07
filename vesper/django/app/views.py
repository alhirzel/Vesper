from collections import defaultdict
import datetime
import itertools
import logging
import json

from django import forms, urls
from django.db.models import Max, Min
from django.core.urlresolvers import reverse
from django.http import (
    HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect)
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
import pytz
import yaml

from vesper.django.app.import_archive_data_form import ImportArchiveDataForm
from vesper.django.app.import_recordings_form import ImportRecordingsForm
from vesper.django.app.models import (
    Annotation, Clip, DeviceConnection, Job, Processor, Recording, Station)
from vesper.django.app.detect_form import DetectForm
from vesper.django.app.export_clips_form import ExportClipsForm
from vesper.singletons import job_manager, preset_manager
from vesper.util.bunch import Bunch
import vesper.ephem.ephem_utils as ephem_utils
import vesper.util.calendar_utils as calendar_utils
import vesper.util.time_utils as time_utils


# A note about UTC vs. local times on client and server:
#
# Our preferred approach to timekeeping is to work with UTC times as
# much as possible, and to convert those times to local times only
# when needed, for example for display to the user.
#
# At present (January 2017), however, we send only local times from
# server to client. I would prefer to send UTC times to the client and
# have it convert them to local times, but it appears that there is not
# yet a reliable way to do that in some browsers. According to
# https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/
# Global_Objects/Date/toLocaleTimeString, though, help is on its way.
# When it becomes easy enough to convert UTC times to local times in
# all of the major browsers we should switch to that practice.


def _create_navbar_items(data):
    return tuple(_create_navbar_item(d) for d in data)


def _create_navbar_item(data):
    if 'url' in data:
        return _create_navbar_link_item(data)
    else:
        return _create_navbar_dropdown_item(data)
    

def _create_navbar_link_item(data):
    name = data['name']
    url = data['url']
    return Bunch(type='link', name=name, url=url)


def _create_navbar_dropdown_item(data):
    name = data['name']
    items = _create_navbar_items(data['dropdown'])
    return Bunch(type='dropdown', name=name, items=items)


# Note that as of 2016-07-19, nested navbar dropdowns do not work.
# The generated HTML looks right to me so the problem may be a
# Bootstrap limitation.
_NAVBAR_ITEMS = _create_navbar_items(yaml.load('''
  
- name: View
  url: calendar
    
- name: Import
  dropdown:
  
      - name: Archive Data
        url: import-archive-data
        
      - name: Recordings
        url: import-recordings
          
- name: Detect
  url: detect
    
- name: Classify
  url: classify
    
- name: Export
  dropdown:
  
      - name: Clip Metadata
        url: export-clip-metadata
        
      - name: Clips
        url: export-clips
  
'''))


_CLASSIFICATIONS = ('Call', 'Call.CHSP', 'Call.WIWA', 'Noise', 'Unknown')
_ONE_DAY = datetime.timedelta(days=1)
_GET_AND_HEAD = ('GET', 'HEAD')


def index(request):
    return redirect(reverse('calendar'))


@csrf_exempt
def detect(request):
    
    if request.method in _GET_AND_HEAD:
        form = DetectForm()
        
    elif request.method == 'POST':
        
        form = DetectForm(request.POST)
        
        if form.is_valid():
            command_spec = _create_detect_command_spec(form)
            job_id = job_manager.instance.start_job(command_spec)
            return _create_job_redirect_response(job_id)
            
    else:
        return HttpResponseNotAllowed(_GET_AND_HEAD)
    
    context = {
        'navbar_items': _NAVBAR_ITEMS,
        'active_navbar_item': 'Detect',
        'form': form
    }
    
    return render(request, 'vesper/detect.html', context)
    
    
def _create_detect_command_spec(form):
    
    data = form.cleaned_data
    
    return {
        'name': 'detect',
        'arguments': {
            'detectors': data['detectors'],
            'stations': data['stations'],
            'start_date': data['start_date'],
            'end_date': data['end_date']
        }
    }


def _create_job_redirect_response(job_id):
    url = urls.reverse('job', args=[job_id])
    return HttpResponseRedirect(url)


def _render_coming_soon(request, action, message):
    context = {
        'navbar_items': _NAVBAR_ITEMS,
        'active_navbar_item': action,
        'message': message
    }
    return render(request, 'vesper/coming-soon.html', context)


def classify(request):
    return _render_coming_soon(
        request, 'Classify', 'Classification is not yet implemented.')
    
    
def import_(request):
    context = {
        'navbar_items': _NAVBAR_ITEMS,
        'active_navbar_item': 'Import'
    }
    return render(request, 'vesper/import.html', context)
    
    
def export_clip_metadata(request):
    return _render_coming_soon(
        request, 'Clip Metadata Export',
        'Clip metadata export is not yet implemented.')


@csrf_exempt
def export_clips(request):
    
    if request.method in _GET_AND_HEAD:
        form = ExportClipsForm()
        
    elif request.method == 'POST':
 
        form = ExportClipsForm(request.POST)
         
        if form.is_valid():
            return _render_coming_soon(
                request, 'Clip Export', 'Clip export is not yet implemented.')
#             command_spec = _create_export_clips_command_spec(form)
#             job_id = job_manager.instance.start_job(command_spec)
#             return _create_job_redirect_response(job_id))
            
    else:
        return HttpResponseNotAllowed(_GET_AND_HEAD)
    
    context = {
        'navbar_items': _NAVBAR_ITEMS,
        'active_navbar_item': 'Export',
        'form': form
    }
    
    return render(request, 'vesper/export-clips.html', context)


def _create_export_clips_command_spec(form):
    
    data = form.cleaned_data
    
    return {
        'name': 'export',
        'arguments': {
            'station_microphones': data['station_mics'],
            'detectors': data['detectors'],
            'start_date': data['start_date'],
            'end_date': data['end_date'],
            'exporter': {
                'name': 'Clip Exporter',
                'arguments': {
                    'output_dir_path': data['output_dir_path'],
                    'clip_file_name_formatter': {
                        'name': 'Simple Clip File Name Formatter',
                        # TODO: Add arguments for format control?
                    }
                }
            }
        }
    }


def stations(request):
    stations = Station.objects.order_by('name')
    context = dict(stations=stations)
    return render(request, 'vesper/stations.html', context)
    
    
def station(request, station_name):
    station = Station.objects.get(name=station_name)
    context = dict(station=station)
    return render(request, 'vesper/station.html', context)

    
def station_clips(request, station_name):
    station = Station.objects.get(name=station_name)
    first_time, last_time = _get_station_clip_start_time_extrema(station)
    context = dict(
        station=station,
        first_time=str(first_time),
        last_time=str(last_time))
    return render(request, 'vesper/station-clips.html', context)

    
def _get_station_clip_start_time_extrema(station):
    # TODO: How expensive is the clip query below? I believe it requires a
    # join on three tables, namely Clip, Recording, and StationDevice.
    clips = Clip.objects.filter(recording__station_recorder__station=station)
    times = clips.aggregate(
        first_time=Min('start_time'),
        last_time=Max('start_time'))
    return (times['first_time'], times['last_time'])


def clips(request):
    first_time, last_time = _get_clip_start_time_extrema()
    context = dict(
        station=station,
        first_time=str(first_time),
        last_time=str(last_time))
    return render(request, 'vesper/clips.html', context)

    
def _get_clip_start_time_extrema():
    times = Clip.objects.aggregate(
        first_time=Min('start_time'),
        last_time=Max('start_time'))
    return (times['first_time'], times['last_time'])


def clip(request, clip_id):
    clip = Clip.objects.get(id=clip_id)
    context = dict(
        clip=clip,
        start_time=str(clip.start_time))
    return render(request, 'vesper/clip.html', context)


def clip_wav(request, clip_id):
    # TODO: Set appropriate response status code on failure, e.g. if
    # there is no such clip or if its sound file is missing.
    clip = Clip.objects.get(id=clip_id)
    content = clip.wav_file_contents
    response = HttpResponse()
    response.write(content)
    response['Content-Type'] = 'audio/wav'
    response['Content-Length'] = len(content)
    return response


def presets_json(request, preset_type_name):
    
    if request.method in _GET_AND_HEAD:
        content = _get_presets_json(preset_type_name)
        return HttpResponse(content, content_type='application/json')
    
    else:
        return HttpResponseNotAllowed(_GET_AND_HEAD)


def _get_presets_json(preset_type_name):
    
    """
    Gets all presets of the specified type as JSON.
    
    The returned JSON is a list of [<preset path>, <preset JSON>]
    pairs, where the preset path is the path relative to the directory
    for the preset type.
    """
        
    presets = preset_manager.instance.get_flattened_presets(preset_type_name)
    presets = [(path, preset.camel_case_data) for path, preset in presets]
    return json.dumps(presets)


@csrf_exempt
def annotation(request, clip_id, annotation_name):
    
    name = annotation_name
    
    if request.method in _GET_AND_HEAD:
        annotation = get_object_or_404(Annotation, name=name, clip__id=clip_id)
        response = HttpResponse()
        response.write(annotation.value)
        return response
    
    elif request.method == 'PUT':
        value = _get_request_body_as_text(request).strip()
        try:
            annotation = Annotation.objects.get(clip__id=clip_id, name=name)
        except Annotation.DoesNotExist:
            clip = get_object_or_404(Clip, id=clip_id)
            annotation = Annotation(clip=clip, name=name, value=value)
        else:
            annotation.value = value
        annotation.save()
        return HttpResponse()

    else:
        return HttpResponseNotAllowed(_GET_AND_HEAD)
    

def _get_request_body_as_text(request):
    
    content_type = _parse_content_type(request.META['CONTENT_TYPE'])
    
    # Make sure content type is text/plain.
    if content_type.name != 'text/plain':
        raise ValueError(
            ('Received HTTP request content type was {}, but {} '
             'is required.').format(content_type.name, 'text/plain'))

    # Get charset, defaulting to us-ascii. (According to rfc6657,
    # us-ascii is the default charset for the text/plain media type.)
    charset = content_type.params.get('charset', 'us-ascii')
            
    return request.body.decode(charset)


# TODO: Does Django already include functions for parsing HTTP headers?
# If so, I couldn't find it.
def _parse_content_type(content_type):
    
    parts = [p.strip() for p in content_type.split(';')]
    
    params = {}
    for part in parts[1:]:
        try:
            name, value = part.split('=', 1)
        except ValueError:
            continue
        params[name] = value
        
    return Bunch(name=parts[0], params=params)
    
    
def calendar(request):
    
    params = request.GET
        
    stations, station_name, station = _get_station_info(params)
    
    station_microphone_outputs = \
        _create_station_microphone_outputs_dict(stations)
    
    microphone_outputs, microphone_output_name, microphone_output = \
        _get_microphone_output_info(station_microphone_outputs, station, params)
        
    detectors, detector_name, detector = _get_detector_info(params)
    
    classifications, classification = _get_classification_info(params)
    
    station_microphone_outputs_json = \
        _get_station_microphone_outputs_json(station_microphone_outputs)
        
    periods_json = _get_periods_json(
        station, microphone_output, detector, 'Classification', classification)
    
    context = {
        'navbar_items': _NAVBAR_ITEMS,
        'active_navbar_item': 'Calendar',
        'stations': stations,
        'station_name': station_name,
        'microphone_outputs': microphone_outputs,
        'microphone_output_name': microphone_output_name,
        'detectors': detectors,
        'detector_name': detector_name,
        'classifications': classifications,
        'classification': classification,
        'station_microphone_outputs_json': station_microphone_outputs_json,
        'periods_json': periods_json
    }
            
    return render(request, 'vesper/calendar.html', context)
    
    
def _get_station_info(params):
    
    stations = Station.objects.order_by('name')
    
    station_name = params.get('station')
    if station_name is None and len(stations) != 0:
        station_name = stations[0].name
        
    if station_name is None:
        station = None
    else:
        station = Station.objects.get(name=station_name)
    
    return stations, station_name, station


def _create_station_microphone_outputs_dict(stations):
    
    """
    Gets a mapping from station names to tuples of microphone output names.
    
    For each station, the value of the mapping is the tuple of the names of
    all outputs of all microphones that were used at the station, sorted
    lexicographically.
    """
    
    return dict(
        _create_station_microphone_outputs_dict_aux(s) for s in stations)


def _create_station_microphone_outputs_dict_aux(station):
    microphone_outputs = _get_station_microphone_outputs(station)
    return (station.name, microphone_outputs)
        

def _get_station_microphone_outputs(station):
    microphones = station.devices.filter(model__type='Microphone')
    output_iterators = [m.outputs.all() for m in microphones]
    outputs = list(itertools.chain.from_iterable(output_iterators))
    outputs.sort(key=lambda o: o.name)
    return tuple(outputs)


def _get_microphone_output_info(station_microphone_outputs, station, params):
    
    if station is None:
        return [], None, None
    
    else:
        
        outputs = station_microphone_outputs[station.name]

        output_name = params.get('microphone')
        if output_name is None and len(outputs) != 0:
            output_name = outputs[0].name
            
        if output_name is None:
            output = None
        else:
            output = _find_object_by_name(outputs, output_name)
            
        return outputs, output_name, output


def _find_object_by_name(objects, name):
    for obj in objects:
        if obj.name == name:
            return obj
    return None

    
def _get_detector_info(params):
    
    detectors = Processor.objects.filter(
        algorithm_version__algorithm__type='Detector').order_by('name')
    
    detector_name = params.get('detector')
    if detector_name is None and len(detectors) != 0:
        detector_name = detectors[0].name
        
    if detector_name is None:
        detector = None
    else:
        detector = Processor.objects.get(name=detector_name)

    return detectors, detector_name, detector

    
def _get_classification_info(params):
    classifications = _add_wildcard_classifications(_CLASSIFICATIONS)
    classification = params.get('classification', classifications[0])
    return classifications, classification


def _get_station_microphone_outputs_json(station_microphone_outputs):
    station_microphone_output_names = dict(
        (station_name, [o.name for o in outputs])
        for station_name, outputs in station_microphone_outputs.items())
    return json.dumps(station_microphone_output_names)


def _get_periods_json(
        station, microphone_output, detector, annotation_name,
        annotation_value):
    
    if station is None or microphone_output is None or detector is None:
        return '[]'
    
    else:
        
        clip_counts = _get_clip_counts(
            station, microphone_output, detector, annotation_name,
            annotation_value)
        
        dates = sorted(list(clip_counts.keys()))
        periods = calendar_utils.get_calendar_periods(dates)
        
        return calendar_utils.get_calendar_periods_json(periods, clip_counts)
    

def _get_recording_channel_num_pairs(
        station, microphone_output, recorder_microphone_infos,
        time_interval=None):
    
    """
    Gets (recording, channel number) pairs for the specified station,
    microphone output, and time interval. Each pair represents a
    channel of a recording made at the specified station from the
    specified microphone output whose interval intersects the specified
    time interval.
    
    The time interval may be `None`, in which case all recordings made at
    the station are returned. The start and/or end times of the time
    interval may also be `None`, denoting times of minus and plus
    infinity, respectively.
    """
    
    # Get recordings for the specified station whose time intervals
    # intersect the specified time interval.
    recordings = _get_recordings(station, time_interval)
         
    # Limit the recordings to those that involved the specified
    # microphone output.
    microphone_output_id = microphone_output.id
    pairs = []
    for r in recordings:
        channel_num = _get_microphone_output_channel_num(
            r, microphone_output_id, recorder_microphone_infos)
        if channel_num is not None:
            pairs.append((r, channel_num))
            
    return pairs
        

def _get_recordings(station, time_interval=None):
    
    """
    Gets the recordings made at the specified station whose time intervals
    intersect the specified time interval.
    
    The time interval may be `None`, in which case all recordings made at
    the station are returned. The start and/or end times of the time
    interval may also be `None`, denoting times of minus and plus
    infinity, respectively.
    """
    
    
    if time_interval is None:
        # no time interval specified
        
        return Recording.objects.filter(station_recorder__station=station)
    
    else:
        
        start, end = time_interval
        
        if start is None and end is None:
            # neither start nor end time specified
            
            return Recording.objects.filter(station_recorder__station=station)
        
        elif end is None:
            # start time specified, but not end time
            
            return Recording.objects.filter(
                station_recorder__station=station).exclude(
                end_time__lte=start)
                
        elif start is None:
            # end time specified, but not start time
            
            return Recording.objects.filter(
                station_recorder__station=station).exclude(
                start_time__gte=end)
                
        else:
            # both start and end times specified
            
            return Recording.objects.filter(
                station_recorder__station=station).exclude(
                end_time__lte=start).exclude(
                start_time__gte=end)

            
def _get_microphone_output_channel_num(
        recording, microphone_output_id, recorder_microphone_infos):
    
    """
    Gets the channel number of the specified microphone output in the
    specified recording, or `None` if the microphone output was not
    involved in the recording.
    
    `recorder_microphone_infos` is a mapping from
    `(recorder_id, microphone_output_id)` pairs to lists of
    `(channel_num, start_time, end_time)` bunches.
    """
    
    # TODO: What should we do a situation in which a single microphone
    # output is connected to more than one recorder input, for example
    # with different input gains? In the following, we simply return
    # the channel number of first input we encounter. Any other inputs
    # are ignored.
    
    recorder = recording.station_recorder.device
    infos = recorder_microphone_infos[(recorder.id, microphone_output_id)]
    start_time = recording.start_time
    
    for info in infos:
        
        if info.start_time <= start_time and start_time < info.end_time:
            # Microphone output was connected to recorder input
            # during recording. (We assume that equipment connections
            # do not change during recordings, so that if a microphone
            # output was connected to a recording's recorder at the
            # beginning of the recording it remained connected througout
            # the recording.)
            
            return info.channel_num
        
    # If we get here, the specified microphone output was not involved
    # in the specified recording.
    return None
        
    
def night(request):
      
    params = request.GET
      
    # TODO: Type check and range check query items.
    station_name = params['station']
    microphone_output_name = params['microphone_output']
    detector_name = params['detector']
    annotation_name = 'Classification'
    annotation_value = params['classification']
    date = params['date']
      
    selected_index = int(params.get('selected', '0')) - 1
    if selected_index == -1:
        selected_index = 'null';
      
    station = Station.objects.get(name=station_name)
    night = time_utils.parse_date(*date.split('-'))
    
    solar_event_times_json = _get_solar_event_times_json(station, night)

    microphone_output = _get_station_microphone_output(
        station, microphone_output_name)
    detector = Processor.objects.get(name=detector_name)
    time_interval = station.get_night_interval_utc(night)
  
    rm_infos = _get_recorder_microphone_infos(station, microphone_output)
    rc_pairs = _get_recording_channel_num_pairs(
        station, microphone_output, rm_infos, time_interval)
    
    recordings = [recording for recording, _ in rc_pairs]
    recordings_json = _get_recordings_json(recordings, station)
    
    annotations = _get_rc_pair_annotations(
        rc_pairs, detector, time_interval, annotation_name, annotation_value)
    clips_json = _get_clips_json(annotations, station)
      
    clip_collection_view_settings_presets_json = \
        _get_presets_json('Clip Collection View Settings')
    annotation_scheme_presets_json = _get_presets_json('Annotation Scheme')
    annotation_commands_presets_json = _get_presets_json('Annotation Commands')
  
    context = {
        'navbar_items': _NAVBAR_ITEMS,
        'active_navbar_item': '',
        'station_name': station_name,
        'microphone_output_name': microphone_output_name,
        'detector_name': detector_name,
        'classification': annotation_value,
        'date': date,
        'solar_event_times_json': solar_event_times_json,
        'recordings_json': recordings_json,
        'clips_json': clips_json,
        'clip_collection_view_settings_presets_json':
            clip_collection_view_settings_presets_json,
        'annotation_scheme_presets_json': annotation_scheme_presets_json,
        'annotation_commands_presets_json': annotation_commands_presets_json
    }
          
    return render(request, 'vesper/night.html', context)
    
        
def _get_rc_pair_annotations(
        rc_pairs, detector, time_interval, annotation_name, annotation_value):
    
    annotation_iterators = [
        _get_annotations(
            recording, channel_num, detector, time_interval,
            annotation_name, annotation_value)
        for recording, channel_num in rc_pairs]
    
    return list(itertools.chain.from_iterable(annotation_iterators))


def _get_solar_event_times_json(station, night):
    
    lat = station.latitude
    lon = station.longitude
    
    if lat is None or lon is None:
        return 'null'
    
    else:
        # have station latitude and longitude
        
        # See note near the top of this file about why we send local
        # instead of UTC times to clients.
        
        utc_to_local = station.utc_to_local
        
        times = {}
        
        # TODO: Fix issue 85 and then simplify the following code.
        
        get = lambda e: _get_solar_event_time(e, lat, lon, night, utc_to_local)
        times['sunset'] = get('Sunset')
        times['civilDusk'] = get('Civil Dusk')
        times['nauticalDusk'] = get('Nautical Dusk')
        times['astronomicalDusk'] = get('Astronomical Dusk')
        
        next_day = night + _ONE_DAY
        get = lambda e: _get_solar_event_time(
            e, lat, lon, next_day, utc_to_local)
        times['astronomicalDawn'] = get('Astronomical Dawn')
        times['nauticalDawn'] = get('Nautical Dawn')
        times['civilDawn'] = get('Civil Dawn')
        times['sunrise'] = get('Sunrise')
        
        return json.dumps(times)

    
def _get_solar_event_time(event, lat, lon, date, utc_to_local):
    utc_time = ephem_utils.get_event_time(event, lat, lon, date)
    local_time = utc_to_local(utc_time)
    return _format_time(local_time)


def _get_station_microphone_output(station, microphone_output_name):
    
    outputs = _get_station_microphone_outputs(station)
    for output in outputs:
        if output.name == microphone_output_name:
            return output
        
    logging.error(
        'Could not find microphone output "{}" for station "{}".'.format(
            microphone_output_name, station.name))
    
    
# def _get_microphone_output_channel_num(recording, microphone_output):
#       
#     # TODO: This function seems somewhat redundant with the
#     # `_get_recording_channel_num_pairs` function. Could they be
#     # combined?
#     
#     connections = DeviceConnection.objects.filter(
#         output=microphone_output,
#         input__device=recording.station_recorder.device)
#     
#     start_time = recording.start_time
#     for connection in connections:
#         if connection.start_time <= start_time and \
#                 connection.end_time >= start_time:
#             return connection.input.channel_num
#     
#     logging.error((
#         'Could not find channel number for microphone output "{}" in '
#         'recording "{}".').format(microphone_output.name, recording.name))


def _get_recordings_json(recordings, station):
    
    # Make sure recordings are in order of increasing start time.
    recordings = sorted(recordings, key=lambda r: r.start_time)
    
    # See note near the top of this file about why we send local
    # instead of UTC times to clients.
    
    utc_to_local = station.utc_to_local
    recording_dicts = [_get_recording_dict(r, utc_to_local) for r in recordings]
    return json.dumps(recording_dicts)

    
def _get_recording_dict(recording, utc_to_local):
    
    start_time = _format_time(utc_to_local(recording.start_time))
    end_time = _format_time(utc_to_local(recording.end_time))
    
    return {
        'startTime': start_time,
        'endTime': end_time
    }
    
    
def _get_clips_json(annotations, station):
    
    # See note near the top of this file about why we send local
    # instead of UTC times to clients.
        
    utc_to_local = station.utc_to_local
    clip_dicts = [_get_clip_dict(a, utc_to_local) for a in annotations]
    return json.dumps(clip_dicts)
    
    
def _get_clip_dict(annotation, utc_to_local):
    
    clip = annotation.clip
    
    # Note about UTC and local times near the top of this file.
    start_time = _format_time(utc_to_local(clip.start_time))
    return {
        'id': clip.id,
        'url': clip.wav_file_url,
        'length': clip.length,
        'sampleRate': clip.sample_rate,
        'startTime': start_time,
        'classification': annotation.value
    }

    
def _format_time(time):
    
    prefix = time.strftime('%Y-%m-%d %H:%M:%S')
    
    millis = int(round(time.microsecond / 1000.))
    millis = '{:03d}'.format(millis)
    while len(millis) != 0 and millis[-1] == '0':
        millis = millis[:-1]
    if len(millis) != 0:
        millis = '.' + millis
        
    time_zone = time.strftime('%Z')
    
    return prefix + millis + ' ' + time_zone
    

def _limit_index(index, min_index, max_index):
    if index < min_index:
        return min_index
    elif index > max_index:
        return max_index
    else:
        return index
    
    
def _add_wildcard_classifications(classifications):
    prefixes = set('*')
    for c in classifications:
        prefixes.add(c)
        prefixes |= _add_wildcard_classifications_aux(c)
    prefixes = list(prefixes)
    prefixes.sort()
    return prefixes
        
        
def _add_wildcard_classifications_aux(classification):
    parts = classification.split('.')
    prefixes = []
    for i in range(1, len(parts)):
        prefix = '.'.join(parts[:i])
        prefixes.append(prefix)
        prefixes.append(prefix + '*')
        prefixes.append(prefix + '.*')
    return frozenset(prefixes)
        
        
def _get_clip_counts(
        station, microphone_output, detector, annotation_name,
        annotation_value):

    time_zone = pytz.timezone(station.time_zone)
    rm_infos = _get_recorder_microphone_infos(station, microphone_output)
    microphone_output_id = microphone_output.id
    
    recordings = Recording.objects.filter(station_recorder__station=station)
    counts = defaultdict(int)
    
    for recording in recordings:
        
        channel_num = _get_microphone_output_channel_num(
            recording, microphone_output_id, rm_infos)
        
        if channel_num is not None:
            
            # TODO: We assume in this function that a recording starts and
            # ends the same night. In the future, we may want to support
            # recordings that span multiple nights. That will look fairly
            # different from what we do here. This function will be replaced
            # by a `_get_clip_count` function that accepts the same arguments
            # as this function plus a `time_interval` argument. The function
            # will be called once for each night, and the `time_interval`
            # argument will specify the interval of the night.
            #
            # The time interval of an archive will not be determined from
            # counts for a particular annotation as it is now, but rather
            # from the intervals of the recordings that are in the archive
            # for a particular station and microphone output.
            
            start_time = recording.start_time
            time_interval = (start_time, recording.end_time)
            
            annotations = _get_annotations(
                recording, channel_num, detector, time_interval,
                annotation_name, annotation_value)
        
            night = _get_night(start_time, time_zone)
            count = len(annotations)
            counts[night] += count
                
#     print('_get_clip_counts:')
#     nights = sorted(counts.keys())
#     for night in nights:
#         print('   ', night, counts[night])
        
    return counts          


def _get_recorder_microphone_infos(station, microphone_output):
    
    """
    Gets a mapping from (recorder_id, microphone_output_id) pairs
    to lists of (channel_num, start_time, end_time) bunches.
    """
    
    # Get recorders that were used at station.
    recorders = station.devices.filter(model__type='Audio Recorder')
    
    rm_infos = defaultdict(list)
    
    for recorder in recorders:
        
        key = (recorder.id, microphone_output.id)
        
        # Get connections from microphone to recorder.
        connections = DeviceConnection.objects.filter(
            output=microphone_output,
            input__device=recorder)
        
        # Remember channel number and time interval of each connection.
        for connection in connections:
            info = Bunch(
                channel_num=connection.input.channel_num,
                start_time=connection.start_time,
                end_time=connection.end_time)
            rm_infos[key].append(info)
            
    return rm_infos
        
        
def _get_night(utc_time, time_zone):
    local_time = utc_time.astimezone(time_zone)
    night = local_time.date()
    if local_time.hour < 12:
        night -= _ONE_DAY
    return night


def _get_night_interval(night, time_zone):
    start_time = _get_local_noon_as_utc_time(night, time_zone)
    end_time = _get_local_noon_as_utc_time(night + _ONE_DAY, time_zone)
    return (start_time, end_time)
    
    
def _get_local_noon_as_utc_time(date, time_zone):
    noon = datetime.time(12)
    dt = datetime.datetime.combine(date, noon)
    dt = time_zone.localize(dt)
    return dt.astimezone(pytz.utc)


# TODO: For the time being, Vesper creates every clip with a
# `Classification` annotation so that the following function will yield
# what we want when `annotation_name` is `Classification` and
# `annotation_value` is `'*'`. But we really want to be able to get both
# clips that have a `Classification` annotation *and clips that do not*.
# What is the best way to do this?
# 
# Since what we're really after is clips rather than annotations, is there
# a query that would give us the clips we want directly? For example, would
# the following yield clips with a specified annotation name and value?
# 
#     Clip.objects.filter(
#         recording=recording,
#         channel_num=channel_num,
#         creating_processor=detector,
#         start_time__range=time_interval,
#         annotations__name=annotation_name,
#         annotations__value=annotation_value)
# 
# A problem I see with this is that once we have a clip, we have to do
# something like:
#
#     clip.annotations.get(name=annotation_name)
#
# in order to get its annotation value, while if we query for annotations
# instead, the annotation value is right there in the result.


def _get_annotations(
        recording, channel_num, detector, time_interval,
        annotation_name, annotation_value):
    
    # TODO: The queries of this function are not strictly correct, in
    # that they exlude clips that start before the query time interval
    # but end after the interval starts. Compare the efficiency of
    # these queries to correct ones. The correct ones will use two
    # `exclude` clauses, one that excludes clips that end before the
    # query interval and another that excludes clips that start after it.
    # Include a comparison of the SQL generated for each approach.
    
    if annotation_value.endswith('*'):
        
        return Annotation.objects.filter(
            clip__recording=recording,
            clip__channel_num=channel_num,
            clip__creating_processor=detector,
            clip__start_time__range=time_interval,
            name=annotation_name,
            value__startswith=annotation_value[:-1])
        
    else:
        
        return Annotation.objects.filter(
            clip__recording=recording,
            clip__channel_num=channel_num,
            clip__creating_processor=detector,
            clip__start_time__range=time_interval,
            name=annotation_name,
            value=annotation_value)


@csrf_exempt
def test_command(request):
    
    if request.method in _GET_AND_HEAD:
        form = forms.Form()
        
    elif request.method == 'POST':
        form = forms.Form(request.POST)
        if form.is_valid():
            print('form valid')
            command_spec = {'name': 'test'}
            job_id = job_manager.instance.start_job(command_spec)
            return _create_job_redirect_response(job_id)
        else:
            print('form invalid')
            
    else:
        return HttpResponseNotAllowed(_GET_AND_HEAD)
    
    return render(request, 'vesper/test-command.html', {'form': form})

    
@csrf_exempt
def import_archive_data(request):
    
    if request.method in _GET_AND_HEAD:
        form = ImportArchiveDataForm()
        
    elif request.method == 'POST':
        
        form = ImportArchiveDataForm(request.POST)
        
        if form.is_valid():
            command_spec = _create_import_archive_data_command_spec(form)
            job_id = job_manager.instance.start_job(command_spec)
            return _create_job_redirect_response(job_id)
            
    else:
        return HttpResponseNotAllowed(_GET_AND_HEAD)
    
    context = {
        'navbar_items': _NAVBAR_ITEMS,
        'active_navbar_item': 'Import',
        'form': form
    }
    
    return render(request, 'vesper/import-archive-data.html', context)
    
    
def _create_import_archive_data_command_spec(form):
    
    data = form.cleaned_data
    
    return {
        'name': 'import',
        'arguments': {
            'importer': {
                'name': 'Archive Data Importer',
                'arguments': {
                    'archive_data': data['archive_data']
                }
            }
        }
    }
            

@csrf_exempt
def import_recordings(request):
    
    if request.method in _GET_AND_HEAD:
        form = ImportRecordingsForm()
        
    elif request.method == 'POST':
        
        form = ImportRecordingsForm(request.POST)
        
        if form.is_valid():
            command_spec = _create_import_recordings_command_spec(form)
            job_id = job_manager.instance.start_job(command_spec)
            return _create_job_redirect_response(job_id)
            
    else:
        return HttpResponseNotAllowed(_GET_AND_HEAD)
    
    context = {
        'navbar_items': _NAVBAR_ITEMS,
        'active_navbar_item': 'Import',
        'form': form
    }
    
    return render(request, 'vesper/import-recordings.html', context)
    
    
def _create_import_recordings_command_spec(form):
    
    data = form.cleaned_data
    
    # TODO: Put select element on form for station name aliases preset.
    data['station_name_aliases_preset'] = 'Station Name Aliases'
    
    return {
        'name': 'import',
        'arguments': {
            'importer': {
                'name': 'Recording Importer',
                'arguments': {
                    'paths': data['paths'],
                    'recursive': data['recursive'],
                    'recording_file_parser': {
                        'name': 'MPG Ranch Recording File Parser',
                        'arguments': {
                            'station_name_aliases_preset':
                                data['station_name_aliases_preset']
                        }
                    }
                }
            }
        }
    }


def job(request, job_id):
    job = get_object_or_404(Job, pk=job_id)
    command_spec = json.loads(job.command)
    context = {
        'navbar_items': _NAVBAR_ITEMS,
        'job': job,
        'command_name': command_spec['name']
    }
    return render(request, 'vesper/job.html', context)
      
          
'''
Some important queries:

* Recordings by station/night/recorder/microphone

    Select on what you can, then use Python, for example for time
    interval intersection.
    
    recordings = Recording.objects.filter(
        station__name=station_name,
        recorder=recorder)
        
    recordings = [
        r for r in recordings
        if _filter_by_time(r, time_interval) and _filter_by_mic(r, microphone)]
        
* Clips (annotations, actually, each of which has a unique clip) for a
  particular recording and annotation:

    recording = ...
    annotation_name = 'Classification'
    annotation_value = 'Call.WIWA'
    annotations = Annotation.objects.filter(
        clip__recording=recording,
        name=annotation_name,
        value=annotation_value)

* Clips (annotations, actually, each of which has a unique clip) for a
  particular station, night, and annotation:

    station_name = 'Baldy'
    time_interval = ...
    annotation_name = 'Classification'
    annotation_value = 'Call.WIWA'
    annotations = Annotation.objects.filter(
        clip__station__name=station_name,
        clip__start_time__range=time_interval,
        name=annotation_name,
        value=annotation_value)

* Clips with combinations of annotations:

    For example, one might want to find the clips that were classified
    differently by two different classifiers.
    
    I don't see a way to do this with a single query. I think we must
    execute a separate query for each separate annotation name and then
    process the query results further in Python.
'''


# The initial implementation of this view generated all of the calendar
# as HTML on the server. See below for the Django template that generated
# the HTML. I switched to sending calendar data as JSON to the client and
# creating DOM elements from Javascript on the client. The JSON approach
# requires that less data be sent to the client. Does it really matter?
# I'm not sure.
#
# def _format_calendar(periods, clip_counts):
#     return [_format_calendar_period(p, clip_counts) for p in periods]
# 
# 
# def _format_calendar_period(period, clip_counts):
#     
#     num_months = period.end - period.start + 1
#     months = list(period.start + i for i in range(num_months))
#     
#     if num_months > 3:
#         
#         num_initial_empty_months = (period.start.month - 1) % 3
#         initial_months = [None] * num_initial_empty_months
#         
#         m = (num_initial_empty_months + num_months) % 3
#         num_final_empty_months = 0 if m == 0 else 3 - m
#         final_months = [None] * num_final_empty_months
#         
#     else:
#         # three or fewer months
#         
#         initial_months = []
#         final_months = [None] * (3 - num_months)
#         
#     months = initial_months + months + final_months
#     
#     rows = []
#     i = 0
#     while i != len(months):
#         row_months = months[i:i + 3]
#         month_infos = [_get_month_info(m, clip_counts) for m in row_months]
#         rows.append(month_infos)
#         i += 3
#         
#     if num_months == 1:
#         # only one month in this period
#         
#         # suppress display of month name since it's same as period name
#         rows[0][0].name = None
#         
#     return Bunch(name=period.name, rows=rows)
#     
#     
# def _get_month_info(month, clip_counts):
#     if month is None:
#         return None
#     else:
#         name = calendar.month_name[month.month]
#         name = '{} {}'.format(name, month.year)
#         days = _get_days_info(month, clip_counts)
#         return Bunch(name=name, days=days)
# 
# 
# def _get_days_info(month, clip_counts):
#     calendar.setfirstweekday(calendar.SUNDAY)
#     days = calendar.monthcalendar(month.year, month.month)
#     days = itertools.chain.from_iterable(days)
#     return [_get_day_info(d, month, clip_counts) for d in days]
# 
# 
# def _get_day_info(day, month, clip_counts):
#     if day == 0:
#         return None
#     else:
#         date = datetime.date(year=month.year, month=month.month, day=day)
#         count = clip_counts.get(date, 0)
#         radius = _get_radius(count)
#         return Bunch(num=day, count=count, radius=radius)
# 
#     
# def _get_radius(count):
#     if count == 0:
#         return 0
#     else:
#         return 25 + 15 * math.log10(count)
#
#
# The following is part of a Django HTML template that was used to construct
# the clip calendar on the server before I switched to constructing the
# calendar on the client using Javascript. I'm not sure which approach is
# better, and each may have different advantages over the other. The template
# is shorter than the Javascript, for example, but using the template requires
# transferring a lot more data from server to client. I wonder if some
# sort of client-side templating facility would be best. My initial
# impression is that the more declarative style of templates makes
# them easier to read than Javascript. A blog post that I came across
# that relates to this is
# www.onebigfluke.com/2015/01/experimentally-verified-why-client-side.html.
# 
#       <div>
#       
#       {% if periods %}
#       
#       {% for period in periods %}
#       
#         <div class="period">
#       
#         <h2 class="period-name">{{period.name}}</h2>
#       
#           {% for row in period.rows %}
#         
#             <div class="row">
#             
#             {% for month in row %}
#            
#               {% if month == None %}
#                 <div class="col-sm-4 month"></div>
#               {% else %}
#                 <div class="col-sm-4 month">
#                   {% if month.name != None %}
#                     <h3 class="month-name">{{month.name}}</h3>
#                   {% endif %}
#                   <div class="month-days">
#                     {% for day in month.days %}
#                       <div class="day">
#                         {% if day == None %}
#                           <div></div>
#                         {% else %}
#                           <div class="circle" data-radius="{{day.radius}}" style="width:{{day.radius}}px;height:{{day.radius}}px"></div>
#                           <a class="day-num" href="#">{{day.num}}</a>
#                         {% endif %}
#                       </div>
#                     {% endfor %}
#                   </div>
#                 </div>
#               {% endif %}
#             
#             {% endfor %}
#             
#             </div>
#           
#           {% endfor %}
#         
#         </div>
#         
#       {% endfor %}
#       
#       {% else %}
#       
#         There are no such clips in the archive.
#         
#       {% endif %}
#       
#       </div>
#       
#     </div>
