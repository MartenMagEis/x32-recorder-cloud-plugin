"""Django-side tests against a small fake Nextcloud - run inside an x32-recorder checkout with this
plugin enabled:

    uv run python x32recorder/manage.py test x32_recorder_cloud_plugin
"""
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from django.test import TestCase, override_settings

from recorder.models import Recording

from .. import login, plugin_config, send_targets, uploader
from ..models import CloudSettings, UploadJob

ROOT_LISTING = """<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">
<d:response><d:href>/remote.php/dav/files/jan/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
<d:response><d:href>/remote.php/dav/files/jan/Predigten/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
<d:response><d:href>/remote.php/dav/files/jan/Gemeinde%20Infos/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
<d:response><d:href>/remote.php/dav/files/jan/notiz.txt</d:href><d:propstat><d:prop><d:resourcetype/></d:prop></d:propstat></d:response>
</d:multistatus>"""
SUB_LISTING = """<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">
<d:response><d:href>/remote.php/dav/files/jan/Predigten/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
<d:response><d:href>/remote.php/dav/files/jan/Predigten/2026/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
</d:multistatus>"""
EMPTY_LISTING = '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"></d:multistatus>'


class FakeNextcloud(BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *args):
        pass

    def _reply(self, status, body=b"", content_type="application/json"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_POST(self):
        self._body()
        base = f"http://127.0.0.1:{self.server.server_port}"
        if self.path == "/index.php/login/v2":
            return self._reply(200, json.dumps({"poll": {"token": "T", "endpoint": base + "/login/v2/poll"},
                                                "login": base + "/login/v2/flow/abc"}))
        if self.path == "/login/v2/poll":
            self.state["polls"] = self.state.get("polls", 0) + 1
            if self.state["polls"] < 2:
                return self._reply(404)
            return self._reply(200, json.dumps({"server": base, "loginName": "jan@example.de", "appPassword": "AP"}))
        self._reply(404)

    def do_GET(self):
        if self.path.startswith("/ocs/v2.php/cloud/user"):
            return self._reply(200, json.dumps({"ocs": {"data": {"id": "jan"}}}))
        self._reply(404)

    def do_PROPFIND(self):
        self._body()
        listing = {"/remote.php/dav/files/jan/": ROOT_LISTING,
                   "/remote.php/dav/files/jan/Predigten/": SUB_LISTING}.get(self.path, EMPTY_LISTING)
        self._reply(207, listing, "application/xml")

    def do_PUT(self):
        body = self._body()
        if self.state.get("full"):
            return self._reply(507)
        self.state.setdefault("uploads", {})[self.path] = body
        self._reply(201)

    def do_DELETE(self):
        self.state["revoked"] = self.headers.get("Authorization")
        self._reply(200, "{}")


class CloudTests(TestCase):
    def setUp(self):
        FakeNextcloud.state = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeNextcloud)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        login.cancel()

    def connect(self):
        plugin_config.update_config_values({"server_url": self.base + "/index.php/login"})
        login.start(CloudSettings.get_solo().server_url, background=False)
        login.wait_for_login(poll_s=0.01, timeout_s=5)

    def test_connect_like_the_nextcloud_apps(self):
        plugin_config.update_config_values({"server_url": self.base + "/index.php/login"})
        login.start(CloudSettings.get_solo().server_url, background=False)
        values = plugin_config.get_config_values()
        self.assertTrue(values["poll"])
        self.assertTrue(values["login_url"].endswith("/login/v2/flow/abc"))
        names = [f["name"] for f in plugin_config.get_config_schema()["fields"]]
        self.assertIn("login_url", names)
        login.wait_for_login(poll_s=0.01, timeout_s=5)
        self.assertEqual(FakeNextcloud.state["polls"], 2)  # first poll: not signed in yet
        settings = CloudSettings.get_solo()
        self.assertEqual((settings.server_url, settings.login_name, settings.user_id, settings.app_password),
                         (self.base, "jan@example.de", "jan", "AP"))
        self.assertEqual(settings.folder_cache, ["Gemeinde Infos", "Predigten", "Predigten/2026"])
        self.assertNotIn("AP", json.dumps(plugin_config.get_config_values()))
        self.assertFalse(plugin_config.get_config_values()["poll"])

    def test_upload_queue_with_retry(self):
        self.connect()
        plugin_config.update_config_values({"folder": "Predigten/2026"})
        self.assertEqual(send_targets.list_targets(), [{"id": 1, "label": "Nextcloud: /Predigten/2026"}])
        with tempfile.TemporaryDirectory() as tmp, override_settings(RECORDING_PATH=tmp):
            rec = Recording.objects.create(state=Recording.STOPPED, channels=[0], name="Gottesdienst 08.11.",
                                           playback_status=Recording.PLAYBACK_READY)
            folder = Path(tmp) / str(rec.uuid)
            folder.mkdir()
            (folder / "playback.mp3").write_bytes(b"ID3 mp3 data")
            send_targets.send(rec, 1)
            job = UploadJob.objects.get()
            self.assertEqual(job.filename, "Gottesdienst 08.11.mp3")

            FakeNextcloud.state["full"] = True
            uploader.process_one()
            job.refresh_from_db()
            self.assertEqual((job.status, job.attempts), (UploadJob.PENDING, 1))
            self.assertIn("Speicherplatz", job.error)
            self.assertIsNone(uploader.process_one())  # not due yet

            FakeNextcloud.state["full"] = False
            job.next_try = None
            job.save()
            uploader.process_one()
            job.refresh_from_db()
            self.assertEqual(job.status, UploadJob.DONE)
            self.assertEqual(FakeNextcloud.state["uploads"],
                             {"/remote.php/dav/files/jan/Predigten/2026/Gottesdienst%2008.11.mp3": b"ID3 mp3 data"})

    def test_no_upload_while_recording(self):
        self.connect()
        rec = Recording.objects.create(state=Recording.STOPPED, channels=[0], name="A")
        uploader.enqueue(rec, "")
        Recording.objects.create(state=Recording.RECORD, channels=[0])
        self.assertIsNone(uploader.process_one())
        self.assertEqual(UploadJob.objects.get().status, UploadJob.PENDING)

    def test_unknown_folder_rejected_and_disconnect_revokes(self):
        from django.core.exceptions import ValidationError

        self.connect()
        with self.assertRaises(ValidationError):
            plugin_config.update_config_values({"folder": "Gibt/es/nicht"})
        plugin_config.handle_config_action("disconnect", {})
        self.assertIsNotNone(FakeNextcloud.state.get("revoked"))
        self.assertFalse(CloudSettings.get_solo().connected)
        self.assertEqual(send_targets.list_targets(), [])

    def test_wrong_address_gives_a_clear_error(self):
        from django.core.exceptions import ValidationError

        plugin_config.update_config_values({"server_url": self.base + "/kein-nextcloud"})
        with self.assertRaises(ValidationError):
            plugin_config.handle_config_action("connect", {})
