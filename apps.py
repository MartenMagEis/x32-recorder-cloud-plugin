import threading

from django.apps import AppConfig

from recorder.plugin_support import should_start_background_work


class CloudPluginConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    # Must match the folder name this repo gets cloned into by x32-recorder's plugin system
    # (derived from the GitHub URL) - "x32-recorder-cloud-plugin" -> "x32_recorder_cloud_plugin".
    name = "x32_recorder_cloud_plugin"
    verbose_name = "Cloud-Upload"

    def ready(self):
        if not should_start_background_work():
            return
        from .uploader import run_uploader

        threading.Thread(target=run_uploader, daemon=True, name="cloud-upload").start()
