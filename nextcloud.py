"""Minimal Nextcloud client - standard library only.

Login works like Nextcloud's own desktop and mobile apps (Login Flow v2, see the Nextcloud developer
manual "Login Flow"): the server hands out a login URL and a poll token; the user signs in at that
URL in any browser; polling then yields an app password just for this device. It's listed under
"Geräte & Sitzungen" in the user's Nextcloud settings and can be revoked there - the user's own
password never reaches the recorder.
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

USER_AGENT = "x32-recorder"
DAV = "{DAV:}"


class NextcloudError(Exception):
    pass


def normalize_server(url):
    url = (url or "").strip().rstrip("/")
    if not url:
        raise NextcloudError("Bitte die Adresse der Nextcloud angeben")
    if "://" not in url:
        url = "https://" + url
    for suffix in ("/index.php/login", "/index.php", "/login"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def _request(method, url, data=None, headers=None, auth=None, timeout=30):
    headers = dict(headers or {})
    headers.setdefault("User-Agent", USER_AGENT)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        raise NextcloudError(f"Nextcloud nicht erreichbar: {getattr(e, 'reason', e)}") from e


# --- login -----------------------------------------------------------------------------------

def start_login(server):
    """-> (login_url, poll_endpoint, poll_token)"""
    status, body = _request("POST", server + "/index.php/login/v2")
    if status != 200:
        raise NextcloudError(f"Unter dieser Adresse antwortet keine Nextcloud (HTTP {status})")
    try:
        data = json.loads(body)
        return data["login"], data["poll"]["endpoint"], data["poll"]["token"]
    except (ValueError, KeyError) as e:
        raise NextcloudError("Unerwartete Antwort der Nextcloud beim Anmelden") from e


def poll_login(endpoint, token):
    """The credentials once the user has signed in, else None (404 = not yet)."""
    status, body = _request("POST", endpoint, data=urllib.parse.urlencode({"token": token}).encode(),
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
    if status == 404:
        return None
    if status != 200:
        raise NextcloudError(f"Anmeldung fehlgeschlagen (HTTP {status})")
    data = json.loads(body)
    return {"server": data["server"].rstrip("/"), "login_name": data["loginName"], "app_password": data["appPassword"]}


def user_id(server, login_name, app_password):
    """The account's user id - WebDAV paths use it, and it can differ from the login name (e.g. an
    e-mail address)."""
    status, body = _request("GET", server + "/ocs/v2.php/cloud/user?format=json",
                            headers={"OCS-APIRequest": "true"}, auth=(login_name, app_password))
    if status != 200:
        raise NextcloudError(f"Konto konnte nicht abgefragt werden (HTTP {status})")
    return json.loads(body)["ocs"]["data"]["id"]


def revoke(server, login_name, app_password):
    """Deletes this device's app password on the server (best effort)."""
    try:
        _request("DELETE", server + "/ocs/v2.php/core/apppassword",
                 headers={"OCS-APIRequest": "true"}, auth=(login_name, app_password))
    except NextcloudError:
        pass


# --- files -----------------------------------------------------------------------------------

def _dav_url(server, user, path=""):
    quoted = "/".join(urllib.parse.quote(p) for p in path.strip("/").split("/") if p)
    return f"{server}/remote.php/dav/files/{urllib.parse.quote(user)}/" + (quoted + "/" if quoted else "")


_PROPFIND = b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>'


def list_folders(server, user, auth, path=""):
    """Direct subfolders of path, as paths relative to the user's root ('Predigten/2026')."""
    status, body = _request("PROPFIND", _dav_url(server, user, path), data=_PROPFIND, auth=auth,
                            headers={"Depth": "1", "Content-Type": "application/xml"})
    if status == 401:
        raise NextcloudError("Anmeldung abgelaufen oder widerrufen - bitte neu verbinden")
    if status != 207:
        raise NextcloudError(f"Ordner konnten nicht gelesen werden (HTTP {status})")
    root_prefix = urllib.parse.urlparse(_dav_url(server, user)).path
    folders = []
    for response in ET.fromstring(body).iter(DAV + "response"):
        href = urllib.parse.unquote(response.findtext(DAV + "href") or "")
        if response.find(f".//{DAV}collection") is None:
            continue
        rel = href[len(urllib.parse.unquote(root_prefix)):].strip("/") if href.startswith(
            urllib.parse.unquote(root_prefix)) else ""
        if rel and rel.strip("/") != path.strip("/"):
            folders.append(rel)
    return sorted(folders, key=str.lower)


def upload(server, user, auth, folder, filename, local_path, timeout=600):
    """PUT the file into folder. Raises NextcloudError."""
    url = _dav_url(server, user, folder) + urllib.parse.quote(filename)
    size = os.path.getsize(local_path)
    with open(local_path, "rb") as f:
        status, _ = _request("PUT", url, data=f, auth=auth, timeout=timeout,
                             headers={"Content-Length": str(size), "Content-Type": "audio/mpeg"})
    if status == 401:
        raise NextcloudError("Anmeldung abgelaufen oder widerrufen - bitte neu verbinden")
    if status == 404 or status == 409:
        raise NextcloudError(f"Zielordner '{folder}' gibt es nicht (mehr)")
    if status == 507:
        raise NextcloudError("Kein Speicherplatz mehr in der Nextcloud")
    if status not in (200, 201, 204):
        raise NextcloudError(f"Upload fehlgeschlagen (HTTP {status})")
