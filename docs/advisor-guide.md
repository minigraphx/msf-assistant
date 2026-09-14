# Persönliche MSF-Beratung

Der lokale Server liefert deinen Roster und speichert Ziele, relevante Angaben
und Empfehlungen. Die angebundene KI recherchiert und formuliert die Beratung.
Ein eigener Chatbot oder Web-Crawler läuft in diesem Paket nicht.

## Einstieg im verbundenen Gespräch

> Prüfe meinen MSF-Datenstand und meine gespeicherten Ziele. Recherchiere die
> entscheidenden aktuellen Quellen und schlage mir ein Hauptziel und zwei
> Alternativen vor. Frage nur nach Angaben, die deine Entscheidung verändern.
> Nach meiner Zielwahl erstelle einen priorisierten Ausbauplan je Charakter
> mit aktuellem und geplantem Level sowie Ausrüstungsstufe. Plane standardmäßig
> kostenlos und speichere mein gewähltes Ziel und den begründeten Plan.

Hosts mit MCP-Prompts können stattdessen `plan_upgrades` mit der konkreten Frage
aufrufen. Derselbe Ablauf wird bei der Server-Initialisierung als Anweisung
übermittelt. Ob die KI diese Anweisungen richtig anwendet, muss im gewählten Host
an den [Abnahmefällen](advisor-acceptance.md) geprüft werden.

Du kannst direkt fragen, wen du als Nächstes ausbauen solltest, wie du DD8
vorbereitest, welche Charaktere langfristig nützlich wirken oder ob sich ein
neues Team lohnt. Für ein angeblich „neuestes“ Team muss zuerst der aktuelle
Veröffentlichungsstand geklärt werden.

## So sollen Antworten aussehen

Die Beratung nennt den Roster-Stand, belegt Anforderungen mit direkten Quellen
und unterscheidet diese von Einschätzungen. Marvel.Church ist die bevorzugte
Guide-Quelle; offizielle Ankündigungen und gegengeprüfte Erfahrungsberichte
ergänzen sie. Alter, Widersprüche und Unsicherheit bleiben sichtbar. Ohne
Recherche gibt es keine Zusicherung zur aktuellen Meta.

Nach deiner Zielwahl enthält der Plan je Charakter Team, aktuellen Level und
Ausrüstungsstand, Ziel-Level und Ziel-Ausrüstungsstufe, Priorität und Begründung.
Materialvorräte begrenzen das Ziel nicht. Gesperrte Charaktere, sinnvolle
Übergangsteams und begründete Umwege werden berücksichtigt. Eine bezahlte Option
bleibt getrennt vom kostenlosen Weg. Fehlende Angaben werden erst bei Bedarf
erfragt; ein starker Roster beweist keinen Event-Abschluss.

## Was dauerhaft gespeichert wird

Standarddatei: `outputs/msf-advisor-context.json`, neben dem Snapshot. Die Datei
ist privat, Git-ignoriert und wird vom Server mit Besitzerrechten geschrieben.
Ein neuer Serverprozess liest sie wieder ein; ein neues Chatgespräch kann Ziele
also wiederaufnehmen. Der Speicher gehört zu genau einem Spieler. Bei mehreren
Konten getrennte Snapshot- und Kontextdateien verwenden.

| Werkzeug | Verwendung |
| --- | --- |
| `get_advisor_context` | Ziele, Nutzerangaben, Empfehlungen und aktuelle Revision lesen |
| `save_goal` | Vorschlag oder gewähltes Ziel anlegen und später korrigieren |
| `save_player_fact` | Eine relevante Angabe speichern; `null` bedeutet ausdrücklich unbekannt |
| `save_recommendation` | Zielbezug, Roster-Zeitpunkt, Quellen, Charakterplan und Unsicherheit speichern |
| `delete_advisor_record` | Auf deinen Wunsch einen Eintrag entfernen |

Ziele unterscheiden `proposed`, `selected`, `paused` und `completed`. Eine
KI-Empfehlung wird dadurch nicht automatisch zu deiner Entscheidung. Jeder
Eintrag erhält Herkunft und Erstellungs-/Änderungszeit; Empfehlungen behalten
zusätzlich den verwendeten Roster-Stand und datierte Quellen.

Bei Korrekturen verwendet die KI die vorhandene `record_id`. Jeder Schreibzugriff
benötigt die zuletzt gelesene `expected_revision`. Hat ein anderes Gespräch
inzwischen gespeichert, wird der veraltete Zugriff abgewiesen: neu lesen,
Änderungen berücksichtigen und nur die gewünschte Korrektur anwenden. Ein
referenziertes Ziel lässt sich erst nach Korrektur oder Entfernung seiner
Empfehlungen löschen.

Fehler vor dem atomaren Ersetzen lassen die bisherige Datei unverändert. Falls
die neue Revision bereits geschrieben wurde, aber die Bestätigung auf dem
Datenträger fehlschlägt, meldet der Server genau diese Unsicherheit samt Revision.
Dann zuerst neu lesen; einen Anlageauftrag nicht blind wiederholen. Eine
beschädigte Datei wird nicht still durch einen leeren Speicher ersetzt.

## Betrieb

`serve --read-only` und `mcp-config --read-only` erlauben nur Lesen. Aktualisierung,
Speichern und Löschen werden dann nicht angeboten. Die KI muss diesen fehlenden
Speicherzugriff offenlegen. Reine Abfragen benötigen keine Anmeldung. Kontext-Schreibzugriffe benötigen
Unix-Dateisperren (macOS/Linux); andere Hosts können den Nur-Lesen-Modus nutzen.

Mit `--context /privater/pfad/context.json` kann der lokale Betreiber einen festen
Speicherort setzen. Verwende einen direkten Dateipfad statt einer symbolischen
Verknüpfung. Werkzeugaufrufe selbst können keine Pfade auswählen. Außerhalb von
`outputs/` muss der Betreiber sicherstellen, dass die Datei privat und vom
jeweiligen Repository ausgeschlossen bleibt.

Zum vollständigen Zurücksetzen den Server beenden und die Kontextdatei lokal
sichern oder entfernen. Der Roster-Snapshot und die Anmeldung sind davon getrennt.
Private Daten nicht nach GitHub oder Linear kopieren.

Bei Nutzung prüft die KI das Datenalter und versucht bei fehlenden/veralteten
Daten einmal einen erlaubten Refresh. Nach einem Fehler bleibt der Altstand
gekennzeichnet. Einen täglichen Hintergrunddienst richtet das Paket nicht ein.
Weitere Details: [Verbindung](chatgpt-connection.md) und
[Datenabdeckung](data-coverage.md).
