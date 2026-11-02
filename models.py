from django.db import models


class CloudSettings(models.Model):
    """The connected Nextcloud account (one row). The app password is never shown in the UI."""

    server_url = models.CharField(
        "Nextcloud-Adresse", max_length=300, blank=True, default="",
        help_text="Z.B. https://cloud.example.de - danach 'Mit Nextcloud verbinden'."
    )
    login_name = models.CharField(max_length=200, blank=True, default="")
    user_id = models.CharField(max_length=200, blank=True, default="")
    app_password = models.CharField(max_length=200, blank=True, default="")
    folder = models.CharField(
        "Zielordner", max_length=500, blank=True, default="",
        help_text="Hierhin werden Aufnahmen hochgeladen."
    )
    # Folders offered for selection - read on connecting and on "Ordnerliste neu laden", not on
    # every page view (that would wait for the network each time).
    folder_cache = models.JSONField(default=list, blank=True)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def connected(self):
        return bool(self.server_url and self.user_id and self.app_password)

    @property
    def auth(self):
        return (self.login_name, self.app_password)


class UploadJob(models.Model):
    PENDING, UPLOADING, DONE, FAILED = "pending", "uploading", "done", "failed"

    recording = models.ForeignKey("recorder.Recording", on_delete=models.CASCADE, related_name="+")
    folder = models.CharField(max_length=500, blank=True, default="")
    filename = models.CharField(max_length=300)
    status = models.CharField(max_length=20, default=PENDING)
    attempts = models.IntegerField(default=0)
    error = models.TextField(blank=True, default="")
    next_try = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    finished = models.DateTimeField(null=True, blank=True)
