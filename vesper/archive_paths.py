"""
Module containing archive directory and file paths.

The `archive_paths` attribute of this module is a `Bunch` whose
attributes are the absolute paths of various archive directories.

This module is initialized by the `archive_settings` module, after
that module creates the archive settings.
"""


from vesper.util.bunch import Bunch


archive_paths = None


def initialize(archive_dir_path, archive_settings):
    
    global archive_paths
    
    archive_paths = Bunch(
        archive_dir_path=archive_dir_path,
        sqlite_database_file_path=archive_dir_path / 'Archive Database.sqlite',
        presets_dir_path=archive_dir_path / 'Presets',
        recording_dir_paths=_create_recording_dir_paths(
            archive_dir_path, archive_settings),
        clips_dir_path=archive_dir_path / 'Clips',
        job_logs_dir_path=archive_dir_path / 'Logs' / 'Jobs')
    
    
def _create_recording_dir_paths(archive_dir_path, archive_settings):
    paths = archive_settings.recording_dir_paths
    if len(paths) == 0:
        paths = [archive_dir_path / 'Recordings']
    return paths
