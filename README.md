# MSF Assistant

Ein schlanker, synchroner Python-Client mit lokalem Browser-Login und privaten
Datenauszügen. Eine spätere API- oder MCP-Schicht kann darüber auf den persönlichen
Marvel-Strike-Force-Account zugreifen. Dieses Repository enthält **keine**
Chat-Oberfläche, LLM-Anbindung, Agenten oder Datenbank.

## Funktionsumfang

- OAuth2 Authorization Code Flow einschließlich `state`-Prüfung und Token-Refresh
- Spielerprofil (`player/v1/card`)
- Spielerkader (`player/v1/roster`)
- Inventar (`player/v1/inventory`)
- paginierte Charakter-Stammdaten (`game/v1/characters`)
- injizierbare HTTP-Session, Timeouts, HTTP-Fehler und validierte JSON-Antworten
- lokale Befehle für Browser-Login, Datenaktualisierung und Logout
- OAuth-Tokens im macOS-Schlüsselbund; optional einmaliger Abruf ohne Speicherung
- private JSON-Datei mit Profil, Roster und Inventar

## Voraussetzungen und Installation

- Python 3.12 oder neuer
- eine im MSF Developer Portal registrierte Anwendung
- Client-ID und Client-Secret dieser Anwendung (Typ **Server-Side**)

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,local]'
# Nur wenn .env noch nicht vorhanden ist:
cp -n .env.example .env
```

Anschließend `MSF_CLIENT_ID`, `MSF_CLIENT_SECRET` und die beim Developer Portal
registrierte `MSF_REDIRECT_URI` in `.env` eintragen. Für die lokale Entwicklung
ist `http://localhost:8000/oauth/callback` vorgesehen. Im Portal entspricht das
der Domain `localhost:8000`, dem Redirect-Pfad `/oauth/callback` und deaktiviertem
HTTPS. Als Datenschutz-Pfad kann `/privacy.html` eingetragen werden.
Der lokale Server stellt Callback und Datenschutzseite während des Login-Laufs
bereit. Die lokale Adresse allein startet noch keinen Server.

Ein persönlicher API-Key muss nicht beantragt werden: Die
[offizielle MSF-API-Spezifikation](https://developer.marvelstrikeforce.com/beta/msf-api.json)
veröffentlicht einen gemeinsamen Wert für den erforderlichen `x-api-key`-Header.
Der Client verwendet diesen standardmäßig. `MSF_API_KEY` bleibt als optionaler
Override verfügbar, falls MSF den öffentlichen Wert ändert. Ein leeres Feld
verwendet ebenfalls den Standard. Dieser öffentliche Wert ersetzt weder
Client-Secret noch den persönlichen OAuth-Token.

Der API-Client sendet ausdrücklich den in der Dokumentation genannten
`User-Agent: APIClient/1.0 (Server)`. Dafür ist kein zusätzlicher Eintrag in
`.env` erforderlich.

Das Client-Secret wird beim Token-Austausch und Refresh per HTTP Basic
Authentication verwendet (`client_secret_basic`). Diese Methode wird in den
[OAuth-Metadaten von MSF](https://hydra-public.prod.m3.scopelypv.com/.well-known/openid-configuration)
als unterstützt aufgeführt. Sie muss auch zur Registrierung der Anwendung
passen. Automatisierte Tests verwenden synthetische Zugangsdaten und ersetzen
keinen echten Login mit der eigenen Anwendung.
Das Secret wird nicht in die Browser-Anmelde-URL oder die API-Header aufgenommen.
Token-Anfragen folgen keinen HTTP-Weiterleitungen.

`.env` und gängige Backup-/Editor-Dateien werden durch Git ignoriert. Zugangsdaten
und Tokens dürfen nicht in Logs, Quellcode oder GitHub landen.

## Lokale Anmeldung und erster Abruf

Die folgenden Befehle werden im Projektordner ausgeführt. Die persistente
Token-Ablage benötigt macOS und das optionale Paket `.[local]`. Der API-Client
selbst bleibt ohne diese Zusatzabhängigkeit nutzbar.

```bash
python -m msf_assistant login
```

Der Browser öffnet die lokale Startseite. Dort **Mit MSF anmelden** auswählen,
bei MSF anmelden und die Freigabe bestätigen. Ein etwaiger macOS-Dialog betrifft
den Zugriff der lokalen Python-Anwendung auf den Schlüsselbund. Die Anmeldung
läuft standardmäßig nach fünf Minuten ab; mit `--timeout 900` sind es 15 Minuten.
Mit `--no-browser` wird nur die lokale Startadresse ausgegeben.

Nach erfolgreicher Anmeldung werden die Tokens im macOS-Schlüsselbund abgelegt.
Anschließend speichert der Client Profil, Roster und Inventar zusammen mit dem
Abrufzeitpunkt in `outputs/msf-snapshot.json`. Die Datei enthält keine Tokens und
ist nur für den aktuellen Benutzer lesbar/schreibbar. Erst nach erfolgreichem
Abruf aller drei Ressourcen wird eine bisherige Datei atomar ersetzt.

```bash
# Gespeicherte Anmeldung verwenden und Daten aktualisieren:
python -m msf_assistant sync

# Lokale Tokens entfernen (MSF-Freigabe und Ausgabedateien bleiben bestehen):
python -m msf_assistant logout

# Einmaliger Login/Abruf ohne Token-Speicherung:
python -m msf_assistant login --no-save
```

`sync` erneuert vorhandene Refresh-Tokens vor dem Abruf. Gibt MSF dabei keinen
neuen Refresh-Token zurück, bleibt der bisherige erhalten. Ohne Refresh-Token
wird der gespeicherte Access-Token verwendet; nach dessen Ablauf ist erneut
`login` nötig. Verweigert der Schlüsselbund den Zugriff, gibt es keinen
automatischen Ausweichweg auf Klartextdateien. `--no-save` ist eine explizite
Alternative für einen einmaligen Abruf.

Für `logout` reichen Client-ID und OAuth-Adresse zur Zuordnung des gespeicherten
Eintrags. Das Client-Secret und die HTTP-Einstellungen werden dabei nicht
benötigt. Die lokale `.env` daher erst nach dem Logout entfernen, falls auch
die Zuordnung zur Anwendung gelöscht werden soll.

Mit `--output outputs/anderer-name.json` lässt sich der Dateiname wählen. Eine
abweichende Konfigurationsdatei wird vor dem Befehl angegeben:
`python -m msf_assistant --env-file /pfad/zur/.env login`.
Nach Installation steht auch der kürzere Befehl `msf-assistant` zur Verfügung.

Der Login-Server bindet ausschließlich an `127.0.0.1`, prüft Host, Callback-Pfad,
OAuth-State und ein Browser-Cookie und akzeptiert jeden Callback nur einmal.
Darum muss der Callback im selben Browser erfolgen, in dem die lokale Anmeldung
gestartet wurde. Anfragen werden nicht protokolliert. Nach Erfolg, Fehler oder
Ablauf wird der Server beendet. Die Datenschutzseite ist eine Beschreibung des
lokalen Testbetriebs und wird nicht öffentlich gehostet.

## Verwendung als Bibliothek

Die Anwendung, die diesen Client einbindet, ist für Redirect und sichere Token-Ablage verantwortlich:

```python
from msf_assistant import MSFAPIClient, MSFOAuth2, Settings

settings = Settings.from_env()
oauth = MSFOAuth2(settings)
authorization_url, state = oauth.authorization_url()

# authorization_url im Browser öffnen und anschließend den Callback verarbeiten.
callback_url = "http://localhost:8000/oauth/callback?code=...&state=..."
code = oauth.parse_callback(callback_url, expected_state=state)
tokens = oauth.exchange_code(code)

client = MSFAPIClient(settings, tokens.access_token)
profile = client.player_profile()
roster = client.player_roster()
inventory = client.inventory()
characters = client.game_characters()
```

`requests`-HTTP-Fehler werden absichtlich an den Aufrufer weitergegeben, damit eine
spätere API/MCP-Schicht Statuscodes korrekt abbilden kann. `MSFAPIError` kennzeichnet
unerwartete JSON-Strukturen. Die Bibliotheksmethoden persistieren selbst keine
Daten; die oben beschriebenen CLI-Befehle übernehmen Token-Ablage und JSON-Export.

## Lokale Arbeitsdateien

Persönliche Roadmaps, Roster-Auswertungen und andere private Ergebnisse gehören
in `outputs/`. Temporäre Arbeitsdateien gehören in `work/`. Beide Ordner sind
durch `.gitignore` ausgeschlossen und bleiben lokal; sie werden nicht mit dem
Repository veröffentlicht. Für diese Dateien bei Bedarf eine lokale Sicherung
außerhalb von GitHub anlegen.

## Entwicklung

```bash
pytest
ruff check .
```

Die Client-Schicht ist unabhängig von einem Webframework. Eine zukünftige MCP- oder REST-Schicht kann `MSFOAuth2` und `MSFAPIClient` importieren, ohne UI- oder LLM-Abhängigkeiten mitzuladen.
