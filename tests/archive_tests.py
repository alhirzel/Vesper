import datetime
import numpy as np
import os
import unittest

import pytz

from nfc.archive.archive import Archive
from nfc.archive.clip_class import ClipClass
from nfc.archive.detector import Detector
from nfc.archive.station import Station
from nfc.util.bunch import Bunch


ARCHIVE_NAME = 'Test Archive'
ARCHIVE_DIR_PATH = ['data', ARCHIVE_NAME]

STATION_TUPLES = [
    ('A', 'Station A', 'US/Eastern'),
    ('B', 'Station B', 'America/Mexico_City')
]
STATIONS = [Station(*t) for t in STATION_TUPLES]

DETECTOR_NAMES = ['Tseep', 'Thrush']
DETECTORS = [Detector(name=n) for n in DETECTOR_NAMES]

CLIP_CLASS_NAMES = ['X', 'X.Z', 'X.Z.W', 'Y']
CLIP_CLASSES = [ClipClass(name=n) for n in CLIP_CLASS_NAMES]


class ArchiveTests(unittest.TestCase):
    
    
    def setUp(self):
        parent_dir_path = os.path.dirname(__file__)
        archive_dir_path = os.path.join(parent_dir_path, *ARCHIVE_DIR_PATH)
        self.archive = Archive.create(
            archive_dir_path, STATIONS, DETECTORS, CLIP_CLASSES)
        self.archive.open()
        
        
    def tearDown(self):
        self.archive.close()
        
        
    def test_name(self):
        self.assertEqual(self.archive.name, ARCHIVE_NAME)
        
        
    def test_add_clip(self):
        self._add_clips()
        
        
    def _add_clips(self):
        
        clips = self._add_clips_aux(
            ('A', 'Tseep', 2012, 1, 2, 20, 11, 12, 0),
            ('A', 'Tseep', 2012, 1, 3, 2, 11, 12, 100),
            ('A', 'Tseep', 2012, 1, 3, 20, 13, 14, 0),
            ('A', 'Thrush', 2012, 1, 2, 20, 11, 13, 0),
            ('B', 'Tseep', 2012, 1, 2, 20, 11, 14, 0)
        )
        
        classifications = (
            (0, 'X'),
            (1, 'Y'),
            (2, 'X.Z'),
            (3, 'X'),
            (0, 'X.Z.W'))
        
        for (i, clip_class_name) in classifications:
            clips[i].clip_class_name = clip_class_name

               
    def _add_clips_aux(self, *args):
        return [self._add_clip(*a) for a in args]
    
    
    def _add_clip(
        self, station_name, detector_name,
        year, month, day, hour, minute, second, ms):
        
        time = datetime.datetime(
            year, month, day, hour, minute, second, ms * 1000, pytz.utc)
        
        sound = Bunch(samples=np.zeros(100), sample_rate=22050.)
        
        return self.archive.add_clip(station_name, detector_name, time, sound)

    
    def test_stations_property(self):
        stations = self.archive.stations
        attribute_names = ('id', 'name', 'long_name')
        expected_values = [((i + 1,) + STATION_TUPLES[i][:-1])
                           for i in xrange(len(STATION_TUPLES))]
        expected_values.sort(key=lambda t: t[1])
        self._assert_objects(stations, attribute_names, expected_values)
        for i in xrange(len(STATION_TUPLES)):
            self.assertEqual(stations[i].time_zone.zone, STATION_TUPLES[i][-1])
        
        
    def _assert_objects(self, objects, attribute_names, expected_values):
        
        self.assertEqual(len(objects), len(expected_values))
        
        for (obj, values) in zip(objects, expected_values):
            
            for (i, name) in enumerate(attribute_names):
                self.assertEqual(getattr(obj, name), values[i])


    def test_detectors_property(self):
        detectors = self.archive.detectors
        attribute_names = ('id', 'name')
        expected_values = [(i + 1, DETECTOR_NAMES[i])
                           for i in xrange(len(DETECTOR_NAMES))]
        expected_values.sort(key=lambda t: t[1])
        self._assert_objects(detectors, attribute_names, expected_values)
        
        
    def test_clip_classes_property(self):
        clip_classes = self.archive.clip_classes
        attribute_names = ('id', 'name')
        expected_values = [(i + 1, CLIP_CLASS_NAMES[i])
                           for i in xrange(len(CLIP_CLASS_NAMES))]
        expected_values.sort(key=lambda t: t[1])
        self._assert_objects(clip_classes, attribute_names, expected_values)
        
        
    def test_start_night_property(self):
        self._add_clips()
        night = self.archive.start_night
        self.assertEquals(night, _to_date((2012, 1, 2)))
        
        
    def test_end_night_property(self):
        self._add_clips()
        night = self.archive.end_night
        self.assertEquals(night, _to_date((2012, 1, 3)))
        
        
    def test_get_clip_counts(self):
        
        cases = (
                 
            (('A', 'Tseep'),
             {(2012, 1, 2): 2, (2012, 1, 3): 1}),
                 
            ((None, None, (2012, 1, 2), (2012, 1, 2)),
             {(2012, 1, 2): 4})
                 
        )
                
        self._add_clips()
        
        for (args, expected_result) in self._create_get_counts_cases(cases):
            result = self.archive.get_clip_counts(*args)
            self.assertEqual(result, expected_result)
        
        
    def _create_get_counts_cases(self, cases):
        return [self._create_get_counts_case(args, result)
                for (args, result) in cases]
    
    
    def _create_get_counts_case(self, args, result):
        return (self._create_get_counts_case_args(*args),
                self._create_get_counts_case_result(result))
            
            
    def _create_get_counts_case_args(
        self, station_name=None, detector_name=None,
        start_night=None, end_night=None, clip_class_name=None):
        
        return (station_name, detector_name,
                _to_date(start_night), _to_date(end_night), clip_class_name)
    
    
    def _create_get_counts_case_result(self, result):
        date = datetime.date
        return dict((date(*triple), count)
                    for (triple, count) in result.iteritems())
    
    
    def test_get_clips(self):
        
        cases = (
                 
            (('A', 'Tseep'),
             [('A', 'Tseep', 2012, 1, 2, 20, 11, 12, 0, 'X.Z.W'),
              ('A', 'Tseep', 2012, 1, 3, 2, 11, 12, 100000, 'Y'),
              ('A', 'Tseep', 2012, 1, 3, 20, 13, 14, 0, 'X.Z')]),
                 
            ((None, None, (2012, 1, 2)),
             [('A', 'Tseep', 2012, 1, 2, 20, 11, 12, 0, 'X.Z.W'),
              ('A', 'Thrush', 2012, 1, 2, 20, 11, 13, 0, 'X'),
              ('B', 'Tseep', 2012, 1, 2, 20, 11, 14, 0, None),
              ('A', 'Tseep', 2012, 1, 3, 2, 11, 12, 100000, 'Y')])

        )
        
#        classifications = (
#            (0, 'X'),
#            (1, 'Y'),
#            (2, 'X.Z'),
#            (3, 'X'),
#            (0, 'X.Z.W'))

        self._add_clips()
        
        for (args, expected_result) in self._create_get_clips_cases(cases):
            result = self.archive.get_clips(*args)
            self._check_get_clips_case_result(result, expected_result)

        
    def _create_get_clips_cases(self, cases):
        return [self._create_get_clips_case(args, result)
                for (args, result) in cases]
    
    
    def _create_get_clips_case(self, args, result):
        return (self._create_get_clips_case_args(*args), result)
            
            
    def _create_get_clips_case_args(
        self, station_name=None, detector_name=None, night=None,
        clip_class_name=None):
        
        return (station_name, detector_name, _to_date(night), clip_class_name)
    
    
    def _check_get_clips_case_result(self, result, expected_result):
        
        self.assertEqual(len(result), len(expected_result))
        
        for (r, er) in zip(result, expected_result):
            self.assertEqual(r.station.name, er[0])
            self.assertEqual(r.detector_name, er[1])
            time = datetime.datetime(*(er[2:9] + (pytz.utc,)))
            self.assertEqual(r.time, time)
            self.assertEqual(r.clip_class_name, er[9])
    
    
    def test_get_clip(self):
        
        cases = (
            ('A', 'Tseep', (2012, 1, 2, 20, 11, 12, 0), 'X.Z.W'),
            ('A', 'Tseep', (2014, 1, 1, 0, 0, 0, 0), None)
        )
        
        self._add_clips()
        
        for station_name, detector_name, time_tuple, expected_result in cases:
            
            time = datetime.datetime(*(time_tuple + (pytz.utc,)))
            result = self.archive.get_clip(station_name, detector_name, time)
            
            if result is None:
                self.assertIsNone(expected_result)
                
            elif result.clip_class_name is None:
                self.assertEqual(expected_result, 'None')
                
            else:
                self.assertEqual(result.clip_class_name, expected_result)
        
        
#     def test_zzz(self):
#         self._add_clips()
        
        
def _to_date(triple):
    return datetime.date(*triple) if triple is not None else None
