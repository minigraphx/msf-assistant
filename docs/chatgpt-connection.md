# Deinen MSF Assistant verbinden

Die lokale App ist ein persönlicher MCP-Server. Sie enthält keine eigene
Chat-Oberfläche. Anmeldung und Aktualisierung benötigen macOS wegen des
Schlüsselbunds; reine Abfragen gespeicherter Daten funktionieren auch auf
anderen Betriebssystemen mit Python 3.12+.

## Nächste Ausbaustufe: eigener Webserver

Die lokale Version ist abgenommen; das Gesamtprojekt bleibt offen. Als Nächstes
soll der MCP auf dem Webserver des Nutzers laufen und unabhängig vom Mac
erreichbar sein. Der Nutzer hat **mehrere Spieler und mehrere Clients** als
Zielumfang bestätigt. Als Ziel ist der bestehende kleine Server über den
SSH-Alias `webserver` vorgesehen; Docker könnte bei Bedarf installiert werden.
Betriebssystem, verfügbare Ressourcen und konkrete Client-Produkte sind noch zu
klären. Die erste SSH-Prüfung am 15. September 2026 scheiterte an der
Schlüsselauthentifizierung, bevor Serverinformationen ausgelesen werden konnten.
Der konfigurierte private Schlüssel ist vorhanden und laut Nutzer
passwortgeschützt; für die weitere Prüfung muss er lokal entsperrt werden.
Auf dem Server wurde nichts verändert.
Die bestehende CLI bietet ausschließlich stdio, und Login/Refresh nutzen
den macOS-Schlüsselbund. Für den Umzug müssen daher Verbindungsweg,
Zugriffsschutz, plattformgeeignete Token-Speicherung und dauerhafter Betrieb
festgelegt werden. Jeder Spieler benötigt eine eigene MSF-Anmeldung sowie
getrennte Spielerdaten und Beratungskontexte. Mehrere zugelassene Clients eines
Spielers sollen denselben persönlichen Kontext nutzen können. Die Zuordnung muss
aus der authentifizierten Identität erfolgen; frei übergebene Spielerkennungen
dürfen keinen Zugriff auf fremde Daten ermöglichen. Die Abnahme muss mindestens
zwei getrennte Testspieler und zwei Clients einschließlich unzulässiger
Zugriffsversuche abdecken. Die konkrete Technik ist noch nicht festgelegt.
Die aktuelle lokale Verbindung bleibt bis zu einer erfolgreichen Serverprobe
in Betrieb. Die Ausbaustufe wird in MIN-118 verfolgt.

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
2. Auf macOS den offiziellen Client mit
   `brew install openai/tools/tunnel-client` installieren. Einen separaten
   **Restricted**-Runtime-Schlüssel mit **Tunnels: Read + Use** anlegen.
   Keinen Admin-Schlüssel verwenden. Den Schlüssel lokal als
   `CONTROL_PLANE_API_KEY` oder über eine `file:/absoluter/pfad`-Referenz
   bereitstellen; nicht ins Repository schreiben. Eine Schlüsseldatei soll
   nur für den eigenen Benutzer lesbar sein (Dateimodus `0600`).
3. Ein stdio-Profil einrichten. Als MCP-Startbefehl die Python-Datei und Argumente
   aus `mcp-config` verwenden; Pfade mit Leerzeichen einzeln quotieren.
4. Das Profil mit `tunnel-client doctor --explain` prüfen. Für einen Lauf im
   geöffneten Terminal `tunnel-client run` verwenden. Für einen länger laufenden
   lokalen Anschluss unterstützt der Client `tunnel-client runtimes connect`;
   anschließend mit `tunnel-client runtimes status <alias> --json` prüfen, ob
   der Prozess läuft und `healthy` sowie `ready` meldet. Der Mac und der Tunnel
   müssen während der Nutzung laufen. Pro Tunnel-ID darf bei stdio nur eine
   Instanz aktiv sein.

Die vollständigen Optionen stehen in `tunnel-client init --help` und
`tunnel-client runtimes connect --help`. Bei einem eigenen Profilverzeichnis
auch bei `doctor` und `run` stets `--profile-dir` angeben oder direkt
`--profile-file` verwenden. Der eingeblendete Kurzaufruf nach `init` enthält
das benutzerdefinierte Profilverzeichnis unter Umständen nicht.
Siehe die [offizielle Client-Anleitung](https://github.com/openai/tunnel-client)
und [Schlüsselberechtigungen](https://github.com/openai/tunnel-client/blob/master/docs/permissions.md).

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

Am 15. September 2026 wurde der vom Nutzer erstellte MSF-Tunnel im angemeldeten
Platform-Konto geprüft. Nach ausdrücklicher Bestätigung wurde der angebotene
ChatGPT-Arbeitsbereich zugeordnet und in ChatGPT der Entwicklermodus aktiviert.
Der offizielle macOS-Client `0.0.14` ist über Homebrew installiert. Ein privates
stdio-Profil mit absoluten MSF-Startpfaden und einer lokalen Schlüsselreferenz
liegt unter `outputs/tunnel/`; dieser Ordner ist von Git ausgeschlossen.

Der Nutzer hat den separaten Runtime-Schlüssel anschließend lokal hinterlegt.
`doctor` besteht. Der verwaltete Anschluss `msf-assistant` läuft mit dem Profil
`outputs/tunnel/msf-assistant-runtime.yaml`; der Status meldet
`process_running=true`, `healthy=true` und `ready=true`. Die authentifizierte
Abfrage der Tunnel-Metadaten ist erfolgreich.

Das private Plugin **MSF Assistant** ist in ChatGPT verbunden. Alle zwölf
Werkzeuge sind sichtbar. Im echten ChatGPT-Gespräch wurden `get_status`,
`get_player_roster(limit=3)` und `get_advisor_context` erfolgreich aufgerufen;
die Antworten wurden in der Werkzeugliste geprüft. Im selben Gespräch
funktionierte die Websuche. Marvel.Church verweigerte den direkten Seitenabruf
mit HTTP 402; ChatGPT kennzeichnete diesen Fehler. Der anschließende direkte
Abruf eines offiziellen MSF-Artikels war erfolgreich. Damit sind private
Datenabfragen und öffentliche Recherche zusammen nachgewiesen. Snapshot und
Beratungskontext blieben bei der Leseprobe unverändert (Hashvergleich).

Der Schlüssel wurde mit 30 Tagen Gültigkeit vorbereitet; vor seinem tatsächlichen
Ablauf muss ein gültiger Schlüssel in derselben privaten Datei hinterlegt werden.
Der laufende lokale Anschluss benötigt einen eingeschalteten, erreichbaren Mac.
Ein automatischer Start nach einem Rechnerneustart ist nicht eingerichtet.
Status und gezieltes Beenden sind mit diesen Befehlen möglich:

```bash
tunnel-client runtimes status msf-assistant --json
tunnel-client runtimes stop msf-assistant
```

Für diese lokale Installation liegt der vollständige Startaufruf in
`outputs/tunnel/start.command`. Er enthält ausschließlich feste Startpfade,
Tunnel-ID und Schlüsselreferenz, keinen Schlüsselwert. Die Datei im Finder
öffnen oder im Projektordner ausführen:

```bash
./outputs/tunnel/start.command
```

Der Aufruf verwendet eine bereits laufende Instanz wieder; dies wurde geprüft.
`runtimes connect --alias` allein genügt nicht: Der Client verlangt auch den
MCP-Startbefehl. Danach immer den Status prüfen. Keinen zweiten `run`-Prozess parallel starten.
Private Prüfnachweise liegen unter `outputs/tunnel/`; Gesprächsinhalte und
Rosterwerte werden nicht ins Repository übernommen.

Den Tunnel-Schlüssel nur lokal hinterlegen. Keine MSF-Zugangsdaten in ein Chatfenster oder nach Linear
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
