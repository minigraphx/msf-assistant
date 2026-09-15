# Serverprüfung für den Mehrspielerbetrieb

Stand: 15. September 2026. Vorbereitende Bestandsaufnahme für MIN-118;
kein freigegebenes Implementierungsdesign und noch keine Bereitstellung.
Das Gesamtprojekt bleibt offen. Der lokale Stand v0.3.0 ist abgenommen.

## Bestätigter Bedarf

Der MSF Assistant soll auf dem bestehenden Webserver unabhängig vom Mac laufen.
Mehrere Spieler nutzen jeweils eigene MSF-Anmeldungen, Spielerdaten und
Beratungskontexte. Mehrere Clients desselben Spielers teilen dessen Kontext.
Spieleridentität und Zugriffsrechte müssen serverseitig geprüft werden.
Als Ziel-Clients sind **ChatGPT und Claude** bestätigt. **Jeder Spieler darf
sich selbst anmelden**; eine Einladung durch den Betreiber ist nicht vorgesehen.

## Lesend gemessener Zustand

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
Die Anwendung benötigt Python 3.12 oder neuer. Der reguläre Support für Ubuntu
20.04 endete am 31. Mai 2025; Canonical bietet erweiterte Wartung über Ubuntu Pro
an. Siehe [offizieller Ubuntu-Status](https://ubuntu.com/20-04).

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

Es wurden keine Pakete installiert, Dienste verändert oder Spielerdaten auf
den Server übertragen.

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
