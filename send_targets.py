"""Opt-in convention of x32-recorder (see its plugins/PLUGIN_DEVELOPMENT.md): offers the connected
Nextcloud folder in the recording's "Senden" menu. Sending only queues the upload - it runs in the
background (uploader.py), also when the connection is weak or away for a while."""
from recorder.models import Recording

from . import uploader
from .models import CloudSettings

TARGET_ID = 1


def list_targets():
    settings = CloudSettings.get_solo()
    if not settings.connected:
        return []
    return [{"id": TARGET_ID, "label": f"Nextcloud: /{settings.folder}"}]


def send(recording, target_id):
    settings = CloudSettings.get_solo()
    if not settings.connected:
        raise RuntimeError("Nicht mit einer Nextcloud verbunden")
    if recording.playback_status != Recording.PLAYBACK_READY:
        raise RuntimeError("Browser-Mix ist noch nicht bereit")
    if not uploader.playback_mp3_path(recording).exists():
        raise RuntimeError("Browser-Mix-Datei nicht gefunden")
    uploader.enqueue(recording, settings.folder)
