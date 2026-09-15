# Serverprüfung für den Mehrspielerbetrieb

Stand: 15. September 2026. Vorbereitende Bestandsaufnahme für MIN-118.
Der Nutzer hat den grundsätzlichen Container-Aufbau bestätigt. Die schriftliche
Konkretisierung steht im [Mehrspieler-Entwurf](superpowers/specs/2026-09-15-hosted-multi-player-design.md)
zur Prüfung; noch keine Implementierung oder Bereitstellung.
Das Gesamtprojekt bleibt offen. Der lokale Stand v0.3.0 ist abgenommen.

## Bestätigter Bedarf

Der MSF Assistant soll auf dem bestehenden Webserver unabhängig vom Mac laufen.
Mehrere Spieler nutzen jeweils eigene MSF-Anmeldungen, Spielerdaten und
Beratungskontexte. Mehrere Clients desselben Spielers teilen dessen Kontext.
Spieleridentität und Zugriffsrechte müssen serverseitig geprüft werden.
Als Ziel-Clients sind **ChatGPT und Claude** bestätigt. **Jeder Spieler darf
sich selbst anmelden**; eine Einladung durch den Betreiber ist nicht vorgesehen.

## Ursprüngliche Bestandsaufnahme

Nach Entsperren des vorhandenen, passwortgeschützten SSH-Schlüssels war der
Zugriff erfolgreich. Die feste Agent- und Schlüsselauswahl musste für den
einzelnen Aufruf überschrieben werden; die gespeicherte SSH-Konfiguration
wurde nicht geändert.

| Bereich | Ergebnis |
| --- | --- |
| Betriebssystem | Ubuntu 20.04.3 LTS, x86_64 |
| Prozessoren | 2 logische Prozessoren |
| Arbeitsspeicher | 1.925 MiB gesamt, 222–226 MiB verfügbar bei zwei Messungen |
| Auslagerungsspeicher | Kein Swap eingerichtet |
| Systemlaufwerk | 7,7 GiB gesamt, rund 2,7 GiB frei |
| Laufzeiten | Python 3.8.10; Docker 24.0.5 bereits installiert |
| Bestehender Betrieb | nginx, Webseiten, Mail, Datenbanken und drei laufende Container |
| Container | Zusammen rund 279 MiB RAM bei einer Momentaufnahme |
| Erweiterte Ubuntu-Wartung | `ua status` meldet keine zugeordnete Subscription |

Die Werte sind Momentaufnahmen, kein Lasttest und keine garantierte Reserve.
Die drei Container sind über ihre Compose-Labels derselben LibreChat-Installation
zugeordnet: Anwendung, MongoDB und Meilisearch. Die übrigen genannten Web-, Mail-
und Datenbankdienste laufen außerhalb dieser Container.
Die Anwendung benötigt Python 3.12 oder neuer. Der reguläre Support für Ubuntu
20.04 endete am 31. Mai 2025; Canonical bietet erweiterte Wartung über Ubuntu Pro
an. Siehe [offizieller Ubuntu-Status](https://ubuntu.com/20-04).

## Zusätzliche Datenlaufwerke geprüft

Nach dem Hinweis des Nutzers wurden am 15. September 2026 auch `/mnt/data`,
`/data` und `/var` geprüft. Die ursprüngliche Angabe von 2,7 GiB freiem Speicher
bezog sich ausschließlich auf `/` und beschreibt nicht die gesamte verfügbare
Speicherkapazität des Servers.

| Einhängepunkt | Dateisystem | Größe | Frei |
| --- | --- | ---: | ---: |
| `/` | ext4 | 7,7 GiB | 2,7 GiB |
| `/mnt/data` | ext4, eigenes Laufwerk | 20 GiB | 5,3 GiB |
| `/data` | ext4, eigenes Laufwerk | 20 GiB | 4,8 GiB |
| `/var` | ext4, eigenes Laufwerk | 49 GiB | 28 GiB |

Alle genannten Laufwerke sind schreibbar eingehängt und haben freie Inodes.
Docker verwendet laut eigener Konfiguration `/var/lib/docker` und liegt damit
bereits auf dem großen `/var`-Laufwerk. `/var` ist über UUID dauerhaft in der
Mount-Konfiguration eingetragen. Es ist keine Verlagerung von Docker erforderlich.

Für den Entwurf wird **`/var/lib/msf-assistant`** als privater persistenter
Datenpfad vorgesehen. Der Pfad existiert noch nicht; er wurde bei dieser Prüfung
nicht angelegt. Vor Dienststart muss geprüft werden, dass das erwartete
`/var`-Laufwerk tatsächlich eingehängt ist, damit ein fehlendes Datenlaufwerk
nicht unbemerkt durch einen leeren Datenordner auf `/` ersetzt wird.

Der freie Platz auf `/var` bietet eine deutlich größere Reserve für Daten und
Images als die ursprüngliche Prüfung des Systemlaufwerks vermuten ließ.
Datengrößen, Logrotation und Sicherungen bleiben begrenzt und überwacht.
Eine Sicherung auf demselben Server ersetzt keine Sicherung gegen dessen Ausfall.
Der verfügbare RAM lag bei dieser Nachprüfung bei 511 MiB; die zusätzlichen
Datenträger erhöhen den Arbeitsspeicher nicht. Es wurde nichts verschoben,
installiert oder an der Mount-Konfiguration geändert.

## LibreChat-Container auf Nutzerwunsch entfernt

Am 15. September 2026 hat der Nutzer nach der Auflistung der drei
LibreChat-Container deren Entfernung freigegeben. Die Anwendung wurde zuerst
gestoppt, anschließend MongoDB und Meilisearch; danach wurden genau diese drei
Container entfernt. Die Nachprüfung um etwa 19:13 UTC zeigte:

- `docker ps -a` liefert keine Container mehr; Port 3080 lauscht nicht mehr.
- Verfügbarer Arbeitsspeicher: **514 MiB** gegenüber **222 MiB** unmittelbar
  vor der Entfernung, also etwa **292 MiB zusätzlich verfügbar**.
- Die persistenten Ordner für Datenbank, Suchindex und hochgeladene Dateien sowie
  das vorhandene MongoDB-Volume sind weiterhin vorhanden. Auch Compose-Dateien,
  Konfiguration und Container-Images wurden nicht entfernt.
- Freier Platz auf dem Systemlaufwerk weiterhin rund **2,7 GiB**. Es wurde
  keine allgemeine Docker- oder Volume-Bereinigung ausgeführt.
- nginx, MariaDB, Redis, Dovecot, Postfix und Docker melden weiterhin `active`.
  Web- und Mailports lauschen weiter. Das ist eine Dienstprüfung, kein vollständiger
  Funktionstest aller bestehenden Webseiten und Postfächer.

Die Container können nicht mehr durch ihre bisherige Docker-Neustartregel
starten, weil sie entfernt wurden. Eine spätere Wiederherstellung würde die
Container mit der erhaltenen Konfiguration und den Daten neu anlegen.
Der MSF Assistant wurde noch nicht auf dem Server installiert. Wartungsstatus
und Lasttest bleiben vor der öffentlichen Freigabe zu klären; 514 MiB sind
eine Momentaufnahme und keine zugesicherte Betriebsreserve.

## Betriebsoptionen für den Entwurf

1. **Ein App-Container im vorhandenen Docker, hinter nginx.** Bevorzugter
   Ausgangspunkt auf dem vom Nutzer gewählten Server: Die passende Python-Version
   kann mit der Anwendung bereitgestellt werden. Spielerbezogene Daten und
   verschlüsselte Zugangsdaten brauchen persistenten Speicher außerhalb des
   austauschbaren Containers. Speichergrenzen, Neustartverhalten und ein
   Lasttest mit parallelen Clients gehören zur Abnahme. Die aktuelle knappe
   Reserve und der Wartungsstatus sind vor produktiver Nutzung zu lösen.
2. **Eigener Systemdienst mit separater Python-Umgebung.** Vermeidet ein weiteres
   Container-Image, erfordert aber Bereitstellung und Pflege einer neueren
   Python-Laufzeit auf dem bestehenden System. Die knappe RAM-Reserve und die
   Betriebssystem-Wartung bleiben bestehen.
3. **Separater kleiner Server für den MSF Assistant.** Trennt die neue Anwendung
   vom bestehenden Web- und Mailbetrieb, erfordert jedoch zusätzliche Infrastruktur
   und eine neue Entscheidung des Nutzers. Dies ist eine Alternative, kein
   bereits beauftragter Serverwechsel.

Für Option 1 zunächst eine einzelne App-Instanz und einen kleinen persistenten
Datenspeicher prüfen. Ein zusätzlicher Datenbankdienst ist noch nicht begründet.
Ein Container ersetzt keine Wartung des Hostsystems. Weder das Abschalten
vorhandener Dienste noch ein Betriebssystem-Upgrade oder eine kostenpflichtige
Serververgrößerung sind Teil der durchgeführten Bestandsaufnahme.

## Nächste Entscheidungen und Nachweise

- Das HTTPS-/OAuth- und Speicherkonzept für ChatGPT, Claude und öffentliche
  Selbstanmeldung zur Entscheidung vorlegen.
- Wartungsweg und ausreichende Kapazität für den Zielserver festlegen; keine
  belastbare RAM-Zusage ohne Messung der Serverversion.
- Mit mindestens zwei Spielern und zwei Clients getrennte Zugriffe,
  Token-Erneuerung, gleichzeitige Kontextänderungen und Neustart prüfen.
- Sicherung und Wiederherstellung testen; erst nach erfolgreicher Abnahme die
  bisherige lokale Verbindung umstellen.

Bei der ursprünglichen Bestandsaufnahme wurden keine Pakete installiert,
Dienste verändert oder Spielerdaten übertragen. Die später vom Nutzer
beauftragte Entfernung der LibreChat-Container ist oben gesondert dokumentiert.

## Grundlagen für die gemeinsame Anmeldung

Die offiziellen Anleitungen von [ChatGPT](https://developers.openai.com/plugins/build/auth)
und [Claude](https://claude.com/docs/connectors/building/authentication) beschreiben
OAuth mit PKCE S256 sowie Client-Metadaten bzw. dynamische Client-Registrierung.
Die lokal installierte MCP-Bibliothek 2.2.0 bietet Streamable HTTP und
Schnittstellen für Token-Prüfung und einen OAuth-Anbieter. Die Anwendung nutzt
diese Möglichkeiten derzeit noch nicht.

Die öffentlichen [MSF-Anmeldemetadaten](https://hydra-public.prod.m3.scopelypv.com/.well-known/openid-configuration)
wurden am 15. September 2026 gelesen. Sie nennen OpenID Connect, einen
`userinfo_endpoint`, Signaturschlüssel und den Claim `sub`. Damit ist eine
Wiedererkennung über die verifizierte Kombination aus Aussteller und Subject
ein prüfbarer Entwurfsansatz. Das ist noch kein Nachweis eines vollständigen
Anmeldevorgangs für zwei Spieler. Anzeigenamen oder übergebene Spielerkennungen
dürfen keine Grundlage für die Kontozuordnung sein.

Vorgeschlagener Ablauf: Spieler verbindet den MSF Assistant in ChatGPT oder
Claude, meldet sich beim offiziellen MSF-Anbieter an und erteilt dem jeweiligen
Client Zugriff. Derselbe verifizierte MSF-Nutzer erhält in beiden Clients
denselben persönlichen Datenbereich. MSF-Zugangsdaten bleiben auf dem Server;
die Clients erhalten eigene widerrufbare Berechtigungen. Die bestehende
MSF-App-Registrierung muss den Server-Callback und weitere Spieler tatsächlich
zulassen; dies ist vor der Live-Abnahme zu prüfen.

Zur Umsetzung sind ein bewährtes OAuth-/OIDC-Verfahren, getrennte Datenzugriffe,
verschlüsselte Token-Speicherung, begrenzte Anfragen und Datenmengen sowie
Löschen und Widerrufen des eigenen Zugangs vorzusehen. Die genaue Bibliothekswahl
und Betriebsfreigabe sind noch nicht getroffen. Die öffentliche Selbstanmeldung
ist keine Zusage unbegrenzter gleichzeitiger Nutzung auf dem vorhandenen Server.
