"""Background upload queue: one job per recording to send. Uploads wait while a recording or
playback runs (the disk and USB bus belong to the audio then), and failed attempts are retried with
growing pauses - the internet connection at a venue may be weak or gone for a while."""
import datetime
import logging
import re
import time
from pathlib import Path

from django.db import close_old_connections
from django.utils import timezone

from . import nextcloud
from .models import CloudSettings, UploadJob

logger = logging.getLogger(__name__)

LOOP_S = 10.0
MAX_ATTEMPTS = 8
RETRY_BASE_S = 60  # 1, 2, 4, ... minutes, at most an hour between tries


def playback_mp3_path(recording):
    """The recording's browser mix - same file the web UI plays and downloads as MP3."""
    from recorder.storage import get_recording_path

    return Path(get_recording_path()) / str(recording.uuid) / "playback.mp3"


def remote_filename(recording):
    name = (recording.name or "").strip() or f"Aufnahme {timezone.localtime(recording.date):%Y-%m-%d %H-%M}"
    return re.sub(r'[\\/:*?"<>|]+', "-", name).strip(" .") + ".mp3"


def enqueue(recording, folder):
    return UploadJob.objects.create(recording=recording, folder=folder, filename=remote_filename(recording))


def _busy():
    from recorder.models import Recording

    return Recording.objects.exclude(state=Recording.STOPPED).exists()


def process_one(now=None):
    """Uploads the next due job, if any. Returns the job it worked on (or None)."""
    now = now or timezone.now()
    job = (UploadJob.objects.filter(status=UploadJob.PENDING)
           .exclude(next_try__gt=now).order_by("created").first())
    if job is None or _busy():
        return None
    settings = CloudSettings.get_solo()
    if not settings.connected:
        return None
    job.status = UploadJob.UPLOADING
    job.attempts += 1
    job.save(update_fields=["status", "attempts"])
    try:
        path = playback_mp3_path(job.recording)
        if not path.exists():
            raise nextcloud.NextcloudError("Browser-Mix-Datei nicht gefunden")
        nextcloud.upload(settings.server_url, settings.user_id, settings.auth, job.folder, job.filename, path)
    except Exception as e:
        job.error = str(e)[:500]
        if job.attempts >= MAX_ATTEMPTS:
            job.status = UploadJob.FAILED
            job.finished = timezone.now()
        else:
            job.status = UploadJob.PENDING
            job.next_try = timezone.now() + datetime.timedelta(
                seconds=min(RETRY_BASE_S * 2 ** (job.attempts - 1), 3600))
        job.save()
        logger.warning("Upload of %s failed (attempt %d): %s", job.filename, job.attempts, e)
        return job
    job.status = UploadJob.DONE
    job.error = ""
    job.finished = timezone.now()
    job.save()
    logger.info("Uploaded %s to /%s", job.filename, job.folder)
    return job


def run_uploader():
    # An upload interrupted by a restart starts over.
    UploadJob.objects.filter(status=UploadJob.UPLOADING).update(status=UploadJob.PENDING)
    while True:
        try:
            close_old_connections()
            process_one()
        except Exception:
            logger.exception("upload loop error")
        time.sleep(LOOP_S)
