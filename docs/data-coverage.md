# Datenabdeckung der Beratung

Geprüft am 14. September 2026 anhand des Clients und der Feldnamen des lokalen
Snapshots. Persönliche Werte werden hier nicht veröffentlicht.

| Bereich | Im Assistant vorhanden | Grenze |
| --- | --- | --- |
| Profil | Level, Gesamtstärke, stärkstes Team, Sammlungszähler | Keine vollständige Spielhistorie |
| Roster | Charakter-ID, Level, gearTier, Sterne, Fähigkeiten, ISO, Stärke | Stärke beweist weder Eignung für jeden Modus noch einen Event-Abschluss |
| Inventar | Gegenstand und Menge | Begrenzt die Ausbauplanung dieser Version nicht |
| Katalog | Namen, Eigenschaften, Fähigkeiten, Freischaltsterne, Status | Keine aktuelle Meta-Bewertung; kein Nachweis eines kostenlosen Freischaltwegs |
| Beratungskontext | Gespeicherte Nutzerangaben, Ziele, Empfehlungen und Quellen | Angaben sind mit ihrer Herkunft zu lesen, nicht als zusätzliche API-Beweise |
| Events / Dark Dimension | Im Snapshot nicht enthalten | Fortschritt bleibt unbekannt, bis eine belastbare Angabe vorliegt |

## Was die API zusätzlich beschreibt

Die [offizielle API-Spezifikation](https://developer.marvelstrikeforce.com/beta/msf-api.json)
(beta 0.2.1, geprüft am 14. September 2026) beschreibt
`/player/v1/events` und `/player/v1/events/{eventId}` mit dem zusätzlichen
Scope `m3p.f.pr.act`. Sie liefern qualifizierende Events samt Fortschritt.
`Objective.progress` kann abgeschlossene Stufen, Punkte und Wiederholungen enthalten;
bei Raids bezeichnet es Allianzfortschritt. `/game/v1/events` liefert allgemeine
Eventinformationen.

Diese Routen werden vom aktuellen Client nicht abgefragt; der zusätzliche Scope
wird beim Login nicht angefordert. Ein lückenloses, dauerhaftes DD-/Legendary-
Abschlussarchiv ist damit nicht nachgewiesen. Eine fehlende Event-Zeile wäre kein
Beleg für „nicht abgeschlossen“.

## Konsequenz für die Beratung

Unbekannte Abschlüsse werden bei konkretem Bedarf erfragt und als Nutzerangabe
mit Zeitpunkt gespeichert. Bestehende Aussagen dürfen korrigiert werden. Ein
entsprechender Roster, Besitz eines Charakters oder hohe Stärke ersetzt diesen
Nachweis nicht. Für widersprüchliche Angaben gezielt nachfragen.

## Frische

`get_status` meldet den Abrufstand und einen separaten Katalog-Zeitstempel. Bei
Nutzung eines fehlenden oder über 24 Stunden alten Datenstands soll der
angebundene Assistent einmal `refresh_data` aufrufen, sofern das Werkzeug
verfügbar und die Aktion im Host erlaubt ist. Fehler offenlegen; danach mit
gekennzeichnetem Altstand arbeiten oder gezielt die fehlenden Angaben erfragen.
Ein ausdrücklich gewünschter Refresh ist jederzeit möglich. Kein automatischer
Hintergrunddienst ist eingerichtet. Im Nur-Lesen-Modus ist ein lokales `sync`
erforderlich.
