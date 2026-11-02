# Cloud-Upload für x32-recorder

Plugin für [x32-recorder](https://github.com/tobire42/x32-recorder): lädt Aufnahmen in eine
Nextcloud hoch - den Browser-Mix als MP3, also dieselbe Datei, die die Weboberfläche abspielt.

## Anmelden wie bei den Nextcloud-Apps

1. In den Plugin-Einstellungen die Adresse der Nextcloud eintragen, z.B.
   `https://cloud.example.de`, und **Mit Nextcloud verbinden** drücken.
2. Den Link **Jetzt anmelden** öffnen und sich dort mit dem eigenen Konto anmelden. Der Browser
   braucht dafür Internet - im Pult-WLAN ohne Internet geht das nicht, dann z.B. von zu Hause
   über den Fernzugang.
3. Der Recorder bekommt ein eigenes App-Passwort. Das eigene Passwort sieht er nie, und das
   App-Passwort lässt sich in der Nextcloud unter **Einstellungen → Sicherheit → Geräte &
   Sitzungen** jederzeit widerrufen. **Verbindung trennen** löscht es auch dort.
4. **Zielordner** wählen (zwei Ebenen tief). Neue Ordner in der Nextcloud anlegen und **Ordnerliste
   neu laden**.

## Hochladen

Im Senden-Menü einer Aufnahme erscheint **Nextcloud: /Zielordner**. Hochgeladen wird im
Hintergrund:

- nicht während einer Aufnahme oder Wiedergabe (Speicher und USB gehören dann dem Audio),
- bei Fehlern erneut, mit wachsenden Pausen (1, 2, 4 ... Minuten, höchstens eine Stunde), bis zu
  8 Versuche - das Internet am Aufnahmeort kann schwach oder zeitweise weg sein.

Die letzten Uploads stehen in den Plugin-Einstellungen.

## Tests

```bash
uv run python x32recorder/manage.py test x32_recorder_cloud_plugin   # mit aktiviertem Plugin
```
Die Tests laufen gegen eine kleine nachgebaute Nextcloud, ohne Netz.
