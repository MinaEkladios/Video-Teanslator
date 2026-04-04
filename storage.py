"""
storage.py — Pluggable storage backend.

Switch storage engine by setting FEATURE_FLAGS['S3_STORAGE'] = True in config.py.
Usage:
    from storage import get_instance
    store = get_instance()
    store.save(file_obj, '/path/to/dest')
"""
import os

from flask import current_app


class LocalStorage:
    """Saves files to the local filesystem."""

    def save(self, file, path):
        """Persist a file-like object or Werkzeug FileStorage to *path*."""
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if hasattr(file, 'save'):
            file.save(path)
        else:
            with open(path, 'wb') as fh:
                fh.write(file.read())

    def delete(self, path):
        """Remove a file if it exists. Logs a warning on failure."""
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            current_app.logger.warning('LocalStorage.delete failed for %s: %s', path, exc)

    def get_url(self, path):
        """Return a URL path relative to the uploads folder."""
        upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')
        rel = os.path.relpath(path, upload_folder)
        return '/uploads/' + rel.replace(os.sep, '/')


class S3Storage:
    """Stub — implement when FEATURE_FLAGS['S3_STORAGE'] is True."""

    def save(self, file, path):
        raise NotImplementedError('S3Storage.save is not yet implemented.')

    def delete(self, path):
        raise NotImplementedError('S3Storage.delete is not yet implemented.')

    def get_url(self, path):
        raise NotImplementedError('S3Storage.get_url is not yet implemented.')


def get_instance():
    """Return the appropriate storage backend based on feature flags."""
    flags = current_app.config.get('FEATURE_FLAGS', {})
    if flags.get('S3_STORAGE'):
        return S3Storage()
    return LocalStorage()
