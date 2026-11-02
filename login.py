"""Connecting to a Nextcloud: start the login, wait in the background until the user has signed in
(in another browser tab), then store the app password and read the folder list."""
import logging
import threading
import time

from django.db import close_old_connections

from . import nextcloud
from .models import CloudSettings

logger = logging.getLogger(__name__)

LOGIN_TIMEOUT_S = 20 * 60  # the server's own poll token is valid for 20 minutes
POLL_S = 2.0
FOLDER_DEPTH = 2
MAX_FOLDERS = 400

_state = {"status": "idle", "login_url": "", "error": ""}
_pending = {}  # poll endpoint + token of the login being waited for
_lock = threading.Lock()


def state():
    with _lock:
        return dict(_state)


def _set(**values):
    with _lock:
        _state.update(values)


def start(server_url, background=True):
    """Starts a login and returns the URL to sign in at. Waiting for the sign-in runs in a
    background thread (background=False leaves it to a direct wait_for_login() call, for tests)."""
    server = nextcloud.normalize_server(server_url)
    login_url, endpoint, token = nextcloud.start_login(server)
    _pending.update(endpoint=endpoint, token=token)
    _set(status="waiting", login_url=login_url, error="")
    if background:
        threading.Thread(target=wait_for_login, daemon=True, name="nextcloud-login").start()
    return login_url


def wait_for_login(poll_s=POLL_S, timeout_s=LOGIN_TIMEOUT_S):
    endpoint, token = _pending["endpoint"], _pending["token"]
    end = time.monotonic() + timeout_s
    try:
        while time.monotonic() < end:
            if state()["status"] != "waiting":
                return  # cancelled
            creds = nextcloud.poll_login(endpoint, token)
            if creds:
                _finish(creds)
                return
            time.sleep(poll_s)
        _set(status="error", login_url="", error="Anmeldung nicht innerhalb von 20 Minuten abgeschlossen")
    except Exception as e:
        logger.warning("Nextcloud login failed: %s", e)
        _set(status="error", login_url="", error=str(e))


def _finish(creds):
    close_old_connections()
    uid = nextcloud.user_id(creds["server"], creds["login_name"], creds["app_password"])
    settings = CloudSettings.get_solo()
    settings.server_url = creds["server"]
    settings.login_name = creds["login_name"]
    settings.app_password = creds["app_password"]
    settings.user_id = uid
    settings.save()
    _set(status="idle", login_url="", error="")
    try:
        refresh_folders()
    except nextcloud.NextcloudError as e:
        logger.warning("Reading folders failed: %s", e)


def cancel():
    _set(status="idle", login_url="", error="")


def refresh_folders():
    """Reads the folder tree (two levels deep) into the settings' folder cache."""
    settings = CloudSettings.get_solo()
    if not settings.connected:
        return []
    folders, level = [], [""]
    for _ in range(FOLDER_DEPTH):
        next_level = []
        for path in level:
            found = nextcloud.list_folders(settings.server_url, settings.user_id, settings.auth, path)
            folders += found
            next_level += found
            if len(folders) >= MAX_FOLDERS:
                break
        level = next_level
    settings.folder_cache = sorted(set(folders), key=str.lower)[:MAX_FOLDERS]
    settings.save(update_fields=["folder_cache"])
    return settings.folder_cache


def disconnect():
    settings = CloudSettings.get_solo()
    if settings.connected:
        nextcloud.revoke(settings.server_url, settings.login_name, settings.app_password)
    settings.login_name = settings.user_id = settings.app_password = ""
    settings.folder_cache = []
    settings.save()
    cancel()
