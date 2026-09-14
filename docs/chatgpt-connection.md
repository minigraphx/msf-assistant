# Deinen MSF Assistant verbinden

Die lokale App ist ein persönlicher MCP-Server. Sie enthält keine eigene
Chat-Oberfläche. Anmeldung und Aktualisierung benötigen macOS wegen des
Schlüsselbunds; reine Abfragen gespeicherter Daten funktionieren auch auf
anderen Betriebssystemen mit Python 3.12+.

## Lokaler MCP-Host

Im Projektordner mit der installierten Python-Umgebung ausführen:

```bash
python -m msf_assistant mcp-config
```

Das ausgegebene JSON enthält absolute Startpfade. Hosts mit `mcpServers`-Format
können diesen Eintrag übernehmen. Der Host startet und beendet den Prozess.
`--read-only` unterdrückt Aktualisierung und alle Schreibwerkzeuge für Ziele,
Nutzerangaben und Empfehlungen. Die Daten- und Kontextabfragen benötigen weder
`.env` noch Zugriff auf den Schlüsselbund. Der Beratungskontext liegt standardmäßig
neben dem Snapshot in `msf-advisor-context.json`; mit `--context` kann der lokale
Betreiber eine feste private Datei wählen. Diesen Pfad auch bei späteren Starts
beibehalten, damit Ziele wieder verfügbar sind.

Für Codex ist der entsprechende TOML-Eintrag:

```toml
[mcp_servers.msf_assistant]
command = "/ABSOLUTER/PROJEKTPFAD/.venv/bin/python"
args = ["-m", "msf_assistant", "--env-file", "/ABSOLUTER/PROJEKTPFAD/.env", "serve", "--snapshot", "/ABSOLUTER/PROJEKTPFAD/outputs/msf-snapshot.json"]
tool_timeout_sec = 180
```

Die Pfade durch die Werte aus `mcp-config` ersetzen. Keine MSF-Secrets in die
Host-Konfiguration kopieren. Die Konfiguration erst nach Prüfung der lokalen
Pfade in den Host übernehmen. Sie wird nicht automatisch global installiert.
Siehe [offizielle MCP-Konfiguration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

## ChatGPT über einen privaten Tunnel

OpenAI dokumentiert einen privaten Verbindungsweg über **Secure MCP Tunnel**.
Er benötigt eine Tunnel-ID, einen eigenen Tunnel-Zugangsschlüssel und passende
Konto-/Workspace-Berechtigungen. Diese sind unabhängig von den MSF-Zugangsdaten.
Die aktuelle Anleitung und den Client-Download findest du im
[offiziellen Tunnel-Leitfaden](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

1. Im dort verlinkten Platform-Tunnelbereich einen Tunnel anlegen und deinem
   ChatGPT-Workspace zuordnen.
2. Den offiziellen `tunnel-client` installieren. Den Tunnel-Schlüssel lokal als
   `CONTROL_PLANE_API_KEY` bereitstellen; nicht ins Repository schreiben.
3. Ein stdio-Profil einrichten. Als MCP-Startbefehl die Python-Datei und Argumente
   aus `mcp-config` verwenden; Pfade mit Leerzeichen einzeln quotieren.
4. Das Profil mit `tunnel-client doctor` prüfen und mit `tunnel-client run` starten.
   Der Mac und der Tunnel müssen während der Nutzung laufen.

In ChatGPT Entwicklermodus unter **Einstellungen → Sicherheit und Anmeldung**
aktivieren. Bei den Plugins über **+ → Verbindung → Tunnel** deinen Tunnel
zuordnen, die gefundenen Werkzeuge prüfen und in einem neuen Gespräch aktivieren.
Die Verfügbarkeit hängt vom Konto und den Workspace-Regeln ab.
[Offizielle Verbindungsanleitung](https://developers.openai.com/plugins/deploy/connect-chatgpt).

Nach einer Aktualisierung des Pakets den Server und gegebenenfalls den Tunnel
neu starten und die Werkzeugliste in ChatGPT neu laden.

Ein guter erster Test ist: „Prüfe den MSF-Datenstand und zeige meine drei stärksten
Charaktere.“ Zunächst `get_status`, danach `get_player_roster` mit `limit: 3`
erwarten. Bei Namenssuche muss zuvor einmal `sync --characters` gelaufen sein.

Die App stellt absichtlich keinen öffentlichen HTTP-Port bereit. Für einen
öffentlichen Dienst wären zusätzlich HTTPS, eine eigene Zugriffskontrolle und
ein dauerhaft erreichbarer Betrieb nötig. Der MSF-Callback auf localhost ist
nur für die Anmeldung vorgesehen und kein ChatGPT-MCP-Endpunkt.

## Häufige Startprobleme

- **`No module named msf_assistant`:** Aus dem Projektordner mit derselben
  Python-Umgebung erneut `python -m pip install '.[local,mcp]'` ausführen.
  Eine normale Installation vermeidet insbesondere macOS-Probleme mit als
  versteckt markierten `.pth`-Dateien einer editierbaren Installation.
- **Keine Daten:** Einmal `login`, danach `sync --characters` ausführen.
- **Keine gespeicherte Anmeldung:** Lokal erneut anmelden. Ein Schlüsselbunddialog
  muss am Mac bestätigt werden; das kann ChatGPT nicht stellvertretend tun.
- **Aktualisierung läuft bereits:** Den anderen Vorgang beenden lassen und
  erneut versuchen. Die vorhandenen Daten bleiben währenddessen lesbar.
- **Änderungen am Quellcode:** Bei normaler Installation das Paket erneut
  installieren und den MCP-Host neu starten.


## Verbindungsstand und Abnahme

Am 14. September 2026 wurden die oben verlinkten offiziellen Anleitungen erneut
geprüft. Der private stdio-Weg ist dort beschrieben. Im getrennten Prüf-Browser
führt die Tunnelverwaltung zur Anmeldung; ein angemeldeter Workspace, dessen
Tunnel-Berechtigungen und eine bestehende Verbindung sind damit nicht belegt.
Die lokale Installation allein erzeugt keine ChatGPT-Verbindung.

Nach der Anmeldung zuerst einen vorhandenen passenden Tunnel auswählen oder
einen privaten Tunnel für den eigenen Workspace einrichten. Den Tunnel-Schlüssel
nur lokal hinterlegen. Keine MSF-Zugangsdaten in ein Chatfenster oder nach Linear
kopieren. Das Anlegen von Zugängen und neue Freigaben im Konto muss der Nutzer
selbst bestätigen.

Die Probe ist erfolgreich, wenn ChatGPT echte Antworten auf `get_status`,
`get_player_roster` und `get_advisor_context` erhält. Anschließend eine aktuelle
öffentliche Guide-Seite im selben Gespräch öffnen lassen. Erst dieser Versuch
belegt, dass Recherche und der private MCP für den gewählten Host gemeinsam
funktionieren. Ein allgemeiner Dokumentationshinweis genügt dafür nicht.

Für die Beratung kann der MCP-Prompt `plan_upgrades` verwendet werden, falls der
Host Prompts anbietet. Alternativ den Einstieg aus der
[Beratungsanleitung](advisor-guide.md) verwenden. Die Server-Anweisungen werden
bei der MCP-Initialisierung mitgeliefert. Die vollständige
[Abnahmecheckliste](advisor-acceptance.md) trennt Protokolltests von den vier
echten Spielfragen.

Wenn der gewählte ChatGPT-Workspace den Tunnel oder die Recherche nicht anbietet,
ist der vorhandene lokale MCP-Eintrag für Codex ein vorbereiteter alternativer
Verbindungsweg. Auch dort erst Datenabruf, Kontext und Recherche tatsächlich
prüfen; eine fertige Verbindung wird nicht vorausgesetzt.
