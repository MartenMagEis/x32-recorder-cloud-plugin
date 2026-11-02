"""Settings on x32-recorder's Settings page (plugin_config.py convention)."""
from django.core.exceptions import ValidationError
from django.utils import timezone

from . import login, nextcloud
from .models import CloudSettings, UploadJob

STATUS_LABELS = {UploadJob.PENDING: "wartet", UploadJob.UPLOADING: "lädt hoch", UploadJob.DONE: "fertig",
                 UploadJob.FAILED: "fehlgeschlagen"}


def _field(name, label, type_, help_text="", readonly=True, **extra):
    return {"name": name, "label": label, "type": type_, "help_text": help_text, "readonly": readonly, **extra}


def get_config_schema():
    settings = CloudSettings.get_solo()
    waiting = login.state()["status"] == "waiting"
    fields = [_field("server_url", "Nextcloud-Adresse", "string", CloudSettings._meta.get_field("server_url").help_text,
                     readonly=settings.connected or waiting)]
    if waiting:
        fields += [
            _field("login_url", "Jetzt anmelden (öffnet die Anmeldeseite der Nextcloud)", "link"),
            _field("cancel_login", "Anmeldung abbrechen", "action", readonly=False),
        ]
    elif settings.connected:
        choices = [["", "/ (Hauptordner)"]] + [[f, "/" + f] for f in settings.folder_cache]
        if settings.folder and settings.folder not in settings.folder_cache:
            choices.append([settings.folder, f"/{settings.folder} (nicht mehr gefunden)"])
        fields += [
            _field("folder", "Zielordner", "choice", CloudSettings._meta.get_field("folder").help_text,
                   readonly=False, choices=choices),
            _field("reload_folders", "Ordnerliste neu laden", "action",
                   "Neue Ordner in der Nextcloud anlegen und dann hier neu laden.", readonly=False),
            _field("disconnect", "Verbindung trennen", "action",
                   "Löscht das App-Passwort des Recorders auch in der Nextcloud.", readonly=False,
                   confirm="Verbindung zur Nextcloud trennen? Wartende Uploads bleiben liegen, bis wieder "
                           "verbunden ist."),
        ]
    else:
        fields.append(_field("connect", "Mit Nextcloud verbinden", "action",
                             "Danach erscheint ein Link zur Anmeldeseite - dort mit dem eigenen Konto "
                             "anmelden. Der Recorder bekommt ein eigenes App-Passwort, das sich in der "
                             "Nextcloud unter 'Geräte & Sitzungen' jederzeit widerrufen lässt.",
                             readonly=False))
    fields += [_field("connection", "Verbindung", "string"), _field("uploads", "Letzte Uploads", "string")]
    return {"fields": fields}


def _uploads_text():
    lines = []
    for job in UploadJob.objects.order_by("-created")[:8]:
        line = f"{timezone.localtime(job.created):%d.%m. %H:%M}  {job.filename} -> /{job.folder}: {STATUS_LABELS[job.status]}"
        if job.error and job.status != UploadJob.DONE:
            line += f" ({job.error})"
        lines.append(line)
    return "\n".join(lines) or "–"


def get_config_values():
    settings = CloudSettings.get_solo()
    st = login.state()
    if st["status"] == "waiting":
        connection = "Warte auf die Anmeldung im Browser ..."
    elif settings.connected:
        connection = f"verbunden als {settings.login_name}"
    else:
        connection = "nicht verbunden" + (f" - {st['error']}" if st["error"] else "")
    return {
        "server_url": settings.server_url,
        "folder": settings.folder,
        "login_url": st["login_url"],
        "connection": connection,
        "uploads": _uploads_text(),
        "poll": st["status"] == "waiting",
    }


def update_config_values(data):
    settings = CloudSettings.get_solo()
    if "server_url" in data and not settings.connected:
        settings.server_url = (data["server_url"] or "").strip()
    if "folder" in data and settings.connected:
        folder = (data["folder"] or "").strip("/")
        if folder and folder not in settings.folder_cache:
            raise ValidationError("Diesen Ordner gibt es nicht - Ordnerliste neu laden?")
        settings.folder = folder
    settings.save()
    return get_config_values()


def handle_config_action(action_name, data):
    try:
        if action_name == "connect":
            login.start(CloudSettings.get_solo().server_url)
        elif action_name == "cancel_login":
            login.cancel()
        elif action_name == "reload_folders":
            login.refresh_folders()
        elif action_name == "disconnect":
            login.disconnect()
    except nextcloud.NextcloudError as e:
        raise ValidationError(str(e))
