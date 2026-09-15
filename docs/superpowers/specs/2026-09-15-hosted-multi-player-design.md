# MSF Assistant: gehosteter Mehrspielerbetrieb

Datum: 15. September 2026. Zugehörige Aufgabe: MIN-118.

Der Nutzer hat den grundsätzlichen Aufbau bestätigt: ein kleiner App-Container
auf dem vorhandenen Webserver, Anmeldung über den eigenen MSF-Account, getrennte
Spielerdaten, gemeinsame Nutzung aus ChatGPT und Claude, widerrufbare Zugänge
und Datensicherung. Dieses Dokument konkretisiert den Aufbau für die schriftliche
Entwurfsprüfung. Die Serverversion ist noch nicht implementiert oder bereitgestellt.

## Ziel und Umfang

Jeder Spieler kann sich ohne Einladung selbst anmelden und den MSF Assistant
in ChatGPT und Claude verbinden. Ein Spieler verwendet in beiden Clients
denselben persönlichen Datenbestand und Beratungskontext. Ein fremder Spieler
erhält darauf keinen Zugriff. Der Betrieb ist nach der Umstellung unabhängig
vom Mac des Betreibers.

Die vorhandenen Werkzeuge und der Beratungsablauf der lokalen Version 0.3.0
bleiben die fachliche Grundlage. Es wird kein eigenes Sprachmodell betrieben.
Webrecherche und Antworten erfolgen weiterhin im jeweiligen Chat-Client.
Die lokale stdio-Nutzung und ihr macOS-Schlüsselbund bleiben unterstützt.

## Aufbau und Verantwortlichkeiten

- Ein Python-3.12+-App-Container stellt den HTTPS-MCP-Zugang über den bestehenden
  nginx bereit. Der Container bindet seinen HTTP-Port nur an die lokale
  Schnittstelle des Hosts. nginx übernimmt TLS und die Weiterleitung.
- Ein HTTP-/MCP-Adapter ermittelt vor jedem Werkzeugaufruf die authentifizierte
  Spieleridentität und die erlaubten Aktionen. Werkzeuge bekommen keine frei
  wählbaren Spielerpfade oder Kontokennungen als Argument.
- Ein Anmeldemodul verbindet den MCP-OAuth-Ablauf mit der offiziellen
  MSF-Anmeldung. Die MCP-Bibliothek und etablierte OAuth-/Kryptografiebibliotheken
  übernehmen die Protokoll- und Kryptografiefunktionen. Keine selbst entworfenen
  Tokenformate mit eigener Signatur- oder Verschlüsselungslogik.
- Ein SQLite-Speicher verwaltet interne Spielerkennungen, verifizierte externe
  Identitäten, verschlüsselte MSF-Tokens und widerrufbare Client-Berechtigungen.
  Ein zusätzlicher Datenbankdienst ist für die erste Version nicht vorgesehen.
- Je interner, serverseitig erzeugter Spielerkennung liegt ein eigener privater
  Datenordner mit Snapshot und Beratungskontext vor. Bestehende atomare
  Dateischreibvorgänge und Kontext-Revisionsprüfungen werden weiterverwendet.
- Ein Synchronisierungsmodul aktualisiert ausschließlich die Daten des
  authentifizierten Spielers. Es sperrt gleichzeitige Token-Erneuerung und
  Snapshot-Aktualisierung pro Spieler und begrenzt parallele Aktualisierungen
  für den gesamten Dienst.

## Anmeldung und Selbstregistrierung

1. Der Spieler trägt dieselbe Server-URL in ChatGPT oder Claude als eigenen
   Connector ein. Die erste Version erfordert keine Veröffentlichung in einem
   Anbieter-Verzeichnis.
2. Der Client entdeckt den OAuth-Dienst über die MCP-Metadaten. Als gemeinsame
   Ausgangsbasis dient OAuth Authorization Code mit PKCE S256 und dynamischer
   Client-Registrierung. HTTPS-Callbacks werden exakt registriert und bei jedem
   Codeaustausch geprüft; die Ziel-Clients sind ChatGPT und Claude. Erweiterungen
   für andere Clients gehören nicht zu dieser ersten Abnahme.
3. Die Anwendung beginnt eine eigene, kurzlebige Anmeldung beim offiziellen
   MSF-Anbieter. Der Vorgang ist an die Browser-Sitzung gebunden; getrennte
   zufällige State-Werte schützen den vorgelagerten MSF-Ablauf und den
   nachgelagerten MCP-Ablauf. Wiederverwendete oder abgelaufene Vorgänge scheitern.
4. Nach dem serverseitigen Codeaustausch wird die Identität über den offiziellen
   HTTPS-Userinfo-Endpunkt ermittelt. Ausschließlich der dort mit dem gerade
   erhaltenen MSF-Token bestätigte `sub` zusammen mit dem festgelegten Aussteller
   identifiziert den Spieler. Vom Browser übergebene Kennungen, Anzeigenamen
   oder ungeprüfte Token-Payloads dienen niemals der Kontozuordnung.
5. Bei der ersten Anmeldung entsteht automatisch ein persönlicher Datenbereich.
   Eine weitere Anmeldung mit derselben verifizierten Identität findet diesen
   Bereich wieder. Ein anderer MSF-Account erhält einen anderen Bereich.
6. Eine Bestätigungsseite zeigt dem Spieler den verbundenen Account, den Client
   und dessen angeforderte Rechte. Erst nach der Zustimmung erhält der Client
   einen einmaligen Code und anschließend eigene MCP-Berechtigungen.

Es gibt keine zusätzlichen lokalen Benutzerpasswörter. Der MSF-Login wird beim
offiziellen Anbieter eingegeben. Der Client erhält keine MSF-Access- oder
Refresh-Tokens. Nach einer fehlgeschlagenen oder abgebrochenen Anmeldung werden
keine nutzbaren MCP-Berechtigungen ausgestellt.

Die MSF-Metadaten belegen einen Userinfo-Endpunkt und `sub`; ein realer
Mehrspieler-Ablauf ist noch nachzuweisen. Die vorhandene MSF-App-Registrierung
muss zusätzliche Spieler und den endgültigen HTTPS-Callback zulassen.
Kann sie dies nicht, bleibt die öffentliche Freigabe bis zur passenden
MSF-App-Konfiguration aus. Es gibt keinen Rückfall auf ein gemeinsames Spielerkonto.

## Berechtigungen und dauerhafter Speicher

Jeder Zugriff wird auf Ablauf, Widerruf, Client, Zielressource und Rechte geprüft.
Die persönliche Datenzuordnung verwendet die interne Spielerkennung; die
Client-Kennung dient der einzelnen Berechtigung und erzeugt keinen zweiten
Spielerdatenbestand. Ein Verbindungs- oder Session-Identifier allein berechtigt
zu keinem Zugriff.

Leserechte und schreibende Aktionen werden getrennt geprüft. Refresh gilt als
Aktion, die Spielerdaten und Zugangsdaten verändern kann. Die bestehenden
Werkzeughinweise für Lesen, Schreiben und Löschen bleiben erhalten.

MSF-Tokens werden mit einer etablierten authentifizierten Verschlüsselung
gespeichert. Der Verschlüsselungsschlüssel und die MSF-App-Zugangsdaten liegen
als geschützte Laufzeitdateien außerhalb des Images und des Repositorys.
Opake MCP-Tokens, Autorisierungscodes und Browser-Sitzungskennungen werden
serverseitig nur gehasht gespeichert. Client-Refresh-Tokens rotieren atomar;
Wiederverwendung eines verbrauchten Tokens widerruft dessen Berechtigungsfamilie.
Unterschiedliche Client-Verbindungen beeinflussen einander dabei nicht.

SQLite-Transaktionen sichern Identitätszuordnung, Codeverbrauch, Widerruf und
Token-Rotation. Pro Spieler serialisierte Aktualisierung verhindert, dass
parallele Refresh-Vorgänge neuere MSF-Tokens überschreiben. Bei einem Fehler nach
erfolgreicher Token-Erneuerung bleiben die erneuerten Tokens gespeichert, auch
wenn die anschließende Datenabfrage scheitert. Der bisherige vollständige Snapshot
bleibt erhalten, bis ein neuer vollständig validiert und atomar ersetzt wurde.

Gleichzeitige Änderungen des Beratungskontexts verwenden weiterhin
`expected_revision`: Eine veraltete Änderung wird abgewiesen, statt eine andere
Änderung zu überschreiben. Cache- und Sperreinträge dürfen nicht unbegrenzt mit
der Anzahl registrierter Spieler im Arbeitsspeicher wachsen.

## Kleine Oberfläche und Fehlerverhalten

Eine einfache deutschsprachige Webseite erläutert das Verbinden in ChatGPT und
Claude. Eine persönliche Kontoseite ermöglicht nach MSF-Anmeldung das Anzeigen
der eigenen Verbindungen, deren Widerruf und das Löschen der eigenen Daten.
Zustandsänderungen brauchen CSRF-Schutz und eine ausdrückliche Aktion; das Löschen
erfordert eine Bestätigung und sperrt vorher sämtliche eigenen Berechtigungen.
Ein laufender Refresh darf gelöschte Daten nicht wieder anlegen.

Browser-Sitzungen verwenden Secure-/HttpOnly-Cookies mit geeignetem SameSite-
Verhalten. Nicht angemeldete MCP-Anfragen erhalten 401 mit dem Verweis auf die
Anmeldemetadaten; fehlende Rechte führen zu 403. Abgelaufene MSF-Berechtigungen
führen zu einer Aufforderung zur erneuten Anmeldung. Vorhandene Daten bleiben
mit erkennbarem Aktualitätsstand lesbar, solange die MCP-Berechtigung gültig ist.

Anfragen, Neuregistrierungen, Datenmengen und parallele Refresh-Vorgänge erhalten
konfigurierbare Grenzen. Bei Überschreitung werden Anfragen mit verständlichem
Fehler und gegebenenfalls Retry-After abgewiesen. Öffentliche Selbstanmeldung
bedeutet keine unbegrenzte gleichzeitige Nutzung. Logs enthalten weder Tokens
noch Anmeldecodes, vollständige Callback-Querystrings oder persönliche Roster.

## Betrieb, Migration und Rückweg

Der Dienst läuft mit einem eigenen Benutzer, persistentem Datenverzeichnis,
Neustartregel, Speichergrenze und einer Zustandsprüfung ohne persönliche Daten.
Der persistente Hostpfad ist `/var/lib/msf-assistant` auf dem separaten
`/var`-Laufwerk. Auch Docker speichert seine Images bereits dort unter
`/var/lib/docker`; eine Docker-Verlagerung ist nicht vorgesehen. Vor Dienststart
wird die erwartete Einhängung von `/var` geprüft. Bei fehlendem Datenlaufwerk
startet die App nicht und legt keinen Ersatzbestand auf dem Systemlaufwerk an.
Das Image wird außerhalb des kleinen Zielservers gebaut. Ein neues Image darf
die Daten nicht ersetzen. Datenbank und Spielerdateien werden zusammenhängend
gesichert; das Verfahren blockiert während der konsistenten Sicherung Änderungen.
Verschlüsselungsschlüssel werden getrennt gesichert und in die Wiederherstellungs-
probe einbezogen. Datensicherungen erhalten eine begrenzte Aufbewahrung; Löschung
im aktiven Bestand und verzögerte Entfernung aus Sicherungen werden erklärt.
Vor dem Start eines wiederhergestellten Bestands werden sämtliche restaurierten
Client-Berechtigungen, Anmeldevorgänge, Codes und Browser-Sitzungen ungültig
gemacht. Nutzer müssen sich erneut anmelden. Eine Wiederherstellung geht
zunächst in einen privaten Wartungszustand; der Betreiber gleicht seit der
Sicherung erfolgte Kontolöschungen ab, bevor Selbstanmeldung wieder freigegeben
wird. Dadurch werden frühere Zugänge nicht allein durch das Backup reaktiviert.

Gemessene Ausgangslage nach der separat vom Nutzer beauftragten Entfernung
der drei LibreChat-Container: knapp 2 GB RAM, etwa 511–514 MiB verfügbar, kein Swap
und vorhandene Web-/Maildienste. Die ergänzende Laufwerksprüfung zeigt rund
28 GiB frei auf dem separaten `/var`-Laufwerk; die 2,7 GiB der ersten Prüfung
beziehen sich ausschließlich auf `/`. Weitere Datenlaufwerke sind vorhanden;
siehe Serverprüfung. Die gespeicherten
LibreChat-Daten sind erhalten; es laufen keine Docker-Container mehr.
Ubuntu 20.04 hat keine aktivierte erweiterte Wartung. Vor öffentlicher Freigabe
müssen ein betreuter Wartungsweg und ausreichende Kapazität nachgewiesen sein.
Serververgrößerung, Host-Upgrade oder das Abschalten bestehender Anwendungen
bedürfen einer konkreten Betriebsentscheidung; sie sind durch die Zustimmung
zum App-Aufbau nicht automatisch beauftragt.

Die bisherige lokale Verbindung bleibt während Entwicklung und Abnahme verfügbar.
Persönliche lokale Daten werden nur nach nachgewiesener Zuordnung zur eigenen
Serveridentität importiert. Sie sind niemals Vorlage für neue Spieler.
Die endgültige HTTPS-Adresse wird als Deployment-Konfiguration festgelegt und
vorher in DNS, Zertifikat und MSF-Callback-Registrierung geprüft.

## Abnahme

- Bestehende Tests für lokale CLI, stdio, Beratungskontext und Datenzugriff bleiben
  erfolgreich. Zusätzlich laufen reale HTTP-Protokolltests der Serverversion.
- Zwei getrennte Testspieler verbinden jeweils ChatGPT und Claude. Beide Clients
  eines Spielers sehen denselben Kontext; kein Client sieht fremde Daten.
- Manipulierte Identitäten, fremde Sessions, fehlende Rechte, falsche Callback-
  Adressen, falsches PKCE, abgelaufene und wiederverwendete Codes werden abgewiesen.
- Parallele Kontextänderungen melden Konflikte korrekt. Parallele Token-Refresh-
  Vorgänge verlieren keine erneuerten Tokens; Fehler erhalten den alten Snapshot.
- Widerruf wirkt auf die gewählte Verbindung. Konto-Löschung sperrt alle eigenen
  Verbindungen und kann nicht durch noch laufende Arbeit rückgängig gemacht werden.
- Nach Dienstneustart und Wiederherstellung einer Sicherung sind Identitäten,
  Daten und Kontext korrekt zugeordnet. Gelöschte oder widerrufene Zugänge
  dürfen durch die Wiederherstellung nicht unbemerkt wieder gültig werden.
- Ein Lasttest mit parallelen Clients und Aktualisierungen misst den tatsächlichen
  Speicherbedarf und prüft die vorgesehenen Grenzen. Keine öffentliche Freigabe,
  wenn der Dienst die vorhandenen Anwendungen verdrängt oder instabil läuft.
- Abschließende Tests aus ChatGPT und Claude funktionieren bei ausgeschaltetem
  Mac. Automatisierte Tests ersetzen diesen Nachweis nicht.

## Grundlagen

- [Serverprüfung und Alternativen](../../server-readiness.md)
- [ChatGPT-Anmeldung](https://developers.openai.com/plugins/build/auth)
- [Claude-Anmeldung](https://claude.com/docs/connectors/building/authentication)
- [MSF-OIDC-Metadaten](https://hydra-public.prod.m3.scopelypv.com/.well-known/openid-configuration)

Die Metadaten und lokal installierte MCP-Bibliothek 2.2.0 wurden bei der
Entwurfsarbeit geprüft. Tatsächliche Client-Kompatibilität und die endgültige
Serverfreigabe folgen aus den oben genannten Abnahmetests.
