# MSF Assistant

Ein schlanker, synchroner Python-Client, über den eine spätere API- oder MCP-Schicht auf den persönlichen Marvel-Strike-Force-Account zugreifen kann. Dieses Repository enthält bewusst **keine** Chat-Oberfläche, LLM-Anbindung, Agenten oder Datenbank.

## Funktionsumfang

- OAuth2 Authorization Code Flow einschließlich `state`-Prüfung und Token-Refresh
- Spielerprofil (`player/v1/card`)
- Spielerkader (`player/v1/roster`)
- Inventar (`player/v1/inventory`)
- paginierte Charakter-Stammdaten (`game/v1/characters`)
- injizierbare HTTP-Session, Timeouts, HTTP-Fehler und validierte JSON-Antworten

## Voraussetzungen und Installation

- Python 3.12 oder neuer
- eine im MSF Developer Portal registrierte Anwendung
- Client-ID und Client-Secret dieser Anwendung (Typ **Server-Side**)

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Anschließend `MSF_CLIENT_ID`, `MSF_CLIENT_SECRET` und die beim Developer Portal
registrierte `MSF_REDIRECT_URI` in `.env` eintragen. Für die lokale Entwicklung
ist `http://localhost:8000/oauth/callback` vorgesehen. Im Portal entspricht das
der Domain `localhost:8000`, dem Redirect-Pfad `/oauth/callback` und deaktiviertem
HTTPS. Ein Callback-Server und die beim Portal angegebene Datenschutzseite
müssen noch ergänzt werden; die Adresse allein startet keinen Login.

Ein persönlicher API-Key muss nicht beantragt werden: Die
[offizielle MSF-API-Spezifikation](https://developer.marvelstrikeforce.com/beta/msf-api.json)
veröffentlicht einen gemeinsamen Wert für den erforderlichen `x-api-key`-Header.
Der Client verwendet diesen standardmäßig. `MSF_API_KEY` bleibt als optionaler
Override verfügbar, falls MSF den öffentlichen Wert ändert. Ein leeres Feld
verwendet ebenfalls den Standard. Dieser öffentliche Wert ersetzt weder
Client-Secret noch den persönlichen OAuth-Token.

Das Client-Secret wird beim Token-Austausch und Refresh per HTTP Basic
Authentication verwendet (`client_secret_basic`). Diese Methode wird in den
[OAuth-Metadaten von MSF](https://hydra-public.prod.m3.scopelypv.com/.well-known/openid-configuration)
als unterstützt aufgeführt. Sie muss auch zur Registrierung der Anwendung
passen; ein erfolgreicher echter Login wurde noch nicht geprüft.
Das Secret wird nicht in die Browser-Anmelde-URL oder die API-Header aufgenommen.
Token-Anfragen folgen keinen HTTP-Weiterleitungen.

`.env` und gängige Backup-/Editor-Dateien werden durch Git ignoriert. Zugangsdaten
und Tokens dürfen nicht in Logs, Quellcode oder GitHub landen.

## Verwendung

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

`requests`-HTTP-Fehler werden absichtlich an den Aufrufer weitergegeben, damit eine spätere API/MCP-Schicht Statuscodes korrekt abbilden kann. `MSFAPIError` kennzeichnet unerwartete JSON-Strukturen. Der Client persistiert keine persönlichen Daten.

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
