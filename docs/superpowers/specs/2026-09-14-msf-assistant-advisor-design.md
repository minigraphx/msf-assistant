# MSF Assistant – persönliche Beratung über MCP

Entwurfsstand: 14. September 2026. Im Brainstorming abgestimmter Umfang der ersten Version. Die lokale Beratungsfunktion ist in Version 0.3.0 umgesetzt; den tatsächlichen Prüf- und Verbindungsstand hält [die Abnahmeübersicht](../../advisor-acceptance.md) fest. Die kontospezifische ChatGPT-Verbindung und Nutzerabnahme sind separat nachzuweisen.

Projektplanung: [MSF Assistant in Linear](https://linear.app/minigraphx/project/msf-assistant-32ceda4eb540).

## Ziel und Einstieg

Der Assistant hilft zunächst einem Spieler, sinnvolle Ausbauziele zu wählen und seinen Roster dafür weiterzuentwickeln. Er beantwortet insbesondere: Wen als Nächstes aufwerten? Wie DD8 vorbereiten? Welche Charaktere sind langfristig sinnvoll? Lohnt sich das neueste Team für diesen Account?

Start mit ChatGPT als Oberfläche und dem bestehenden lokalen MCP-Server. Später sind Zugriff unterwegs, Claude, lokale Modelle und weitere Spieler mögliche Erweiterungen. Ziele und Empfehlungen gehören deshalb zum Assistant und dürfen nicht ausschließlich in einem Chat gespeichert sein. Mehrbenutzerbetrieb und eine eigene Oberfläche gehören nicht zur ersten Version.

## Beratungsverhalten

1. Verfügbaren Roster und Datenalter selbstständig prüfen. Stärke bedeutet Roster-Eignung und Engpässe für Raids, Cosmic Crucible, Krieg, Missionen, Events und Dark Dimension; tatsächliche Platzierungen sind kein Ersatz dafür.
2. Dauerhafte Event-Abschlüsse aus verfügbaren Fortschrittsdaten berücksichtigen. API-Abdeckung zuerst prüfen: Roster-Stärke beweist keinen Abschluss. Fehlender Fortschritt bleibt unbekannt.
3. Eine Hauptempfehlung und zwei alternative Ziele mit Begründung vorschlagen. Bei unzureichender Grundlage gezielt nachfragen, statt drei scheinbar sichere Empfehlungen zu erfinden.
4. Keine verpflichtende Bestandsaufnahme. Fehlende Angaben dynamisch erfragen, sobald sie eine Entscheidung beeinflussen; Antworten speichern. Ziele durch Rückfragen schärfen.
5. Nach Zielauswahl einen priorisierten Ausbauplan je Charakter liefern: aktueller Stand, Ziel-Level, Ziel-Ausrüstungsstufe, Reihenfolge und Begründung. Bei Bedarf mehrere Teams berücksichtigen. Vorhandenes Material begrenzt die Planung nicht; Ressourcenoptimierung nach Inventar ist nicht Teil dieser Version.
6. Noch nicht freigeschaltete Charaktere dürfen Teil des Zielteams sein. Eine Übergangslösung aus dem vorhandenen Roster anbieten und zusätzliche Investitionen darin begründen. Voraussetzungen und Verfügbarkeit nicht erfinden.
7. Aktiv einen Umweg empfehlen, wenn er voraussichtlich mehr Fortschritt bringt. Nutzen, Nachteile und Auswirkungen auf das ursprüngliche Ziel erklären; keine unbelegten Zeitprognosen. Die Zielentscheidung bleibt beim Nutzer.
8. Free-to-play ist Standard. Wenn Echtgeld die Empfehlung wesentlich verändert, nach Budget fragen oder eine klar getrennte bezahlte Alternative nennen; einen kostenlosen Weg beibehalten.

## Wissen und Nachvollziehbarkeit

Marvel.Church ist die bevorzugte Quelle für Guides und Bewertungen. Offizielle Ankündigungen liefern Informationen zu neuen Charakteren und Events. Reddit ergänzt Erfahrungsberichte, die gegengeprüft werden müssen. Das ist eine Quellenpräferenz, keine pauschale Zusicherung der Richtigkeit.

Empfehlungen verbinden Account-Daten, Nutzerziele und aktuelle Quellen. Relevante Quellen mit Datum oder Abrufstand nennen; Anforderungen, Community-Einschätzungen und eigene Schlussfolgerungen unterscheiden. Widersprüche und Unsicherheit sichtbar machen. Langfristige Stärke ist eine Prognose, keine Garantie. Ohne aktuelle Recherche keine aktuelle Meta behaupten.

In Version 1 soll der angebundene Assistent recherchieren; ein eigener Web-Crawler oder Wissensdienst ist nicht vorgesehen. Im ersten Meilenstein prüfen, ob Recherche und MCP in der gewählten ChatGPT-Konfiguration zusammen nutzbar sind. Wenn nicht, die Einschränkung dokumentieren und eine konkrete Alternative zur Entscheidung vorlegen.

## Architektur und Datenfluss

- Bestehende OAuth-, Schlüsselbund- und Snapshot-Schicht weiterverwenden. MCP liefert Account-Abfragen und explizite Aktualisierung; keine Spielaktionen oder Käufe.
- Den MCP um eng begrenztes Lesen und Schreiben des persönlichen Beratungskontexts erweitern. Tool-Aufrufer dürfen keine beliebigen Dateipfade wählen.
- Lokal im ignorierten privaten Ausgabebereich Ziele, Nutzerangaben und Empfehlungen speichern. Angaben erhalten Herkunft und Zeitpunkt; Empfehlungen zusätzlich Roster-Datenstand, verwendete Quellen und Bezug zum Ziel. Nutzerkorrekturen müssen möglich sein. Genaue Schemas und Werkzeugnamen folgen im Implementierungsplan.
- ChatGPT liest Snapshot und Beratungskontext, recherchiert die entscheidungsrelevanten Informationen, fragt bei Bedarf nach und erstellt Vorschläge. Gewählte Ziele und relevante Antworten werden gespeichert. Vorschläge und vom Nutzer gewählte Ziele bleiben unterscheidbar.
- Bei späteren Fragen aktuellen Roster mit bisherigen Empfehlungen vergleichen und Änderungen begründen. Persönliche Angaben und Spielerdaten bleiben außerhalb von Git und Linear; dort liegen nur Konzept und Entwicklungsaufgaben.

## Aktualität und Fehlerverhalten

Ein täglicher Datenstand reicht. Nach Änderungen ist gezielte Aktualisierung möglich; kein Refresh bei jeder Frage. Als einfacher Vorschlag für die lokale Version gilt: bei Nutzung einen über 24 Stunden alten Stand erkennen und die vorhandene Aktualisierung nutzen. Eine tägliche Hintergrundplanung ist damit noch nicht eingerichtet; der genaue Auslöser wird im ersten Meilenstein festgelegt.

Bei fehlendem Zugriff, abgelaufener Anmeldung oder fehlgeschlagenem Refresh den Fehler und den vorhandenen Datenstand verständlich anzeigen. Einen vorhandenen Snapshot erhalten, veraltete Daten kennzeichnen und keine Live-Daten vortäuschen. Kontext atomar speichern; Fehler dürfen nicht als erfolgreiche Speicherung dargestellt werden. Gleichzeitige Schreibzugriffe müssen ohne verlorene Aktualisierungen behandelt werden.

Lokaler Betrieb setzt einen laufenden Mac und eine funktionierende Verbindung voraus. Der konkrete, zum Konto passende ChatGPT-Verbindungsweg wird aktuell verifiziert; vorhandene Verbindungsdokumentation ist kein Nachweis einer bereits funktionierenden Verbindung.

## Meilensteine und Abnahme

### 1. ChatGPT verbinden und Datenabdeckung prüfen

Bestehenden Server mit ChatGPT verbinden; Status und Roster Ende zu Ende lesen. Recherche zusammen mit MCP prüfen. Fortschritts- und Event-Daten nach verfügbar/nicht verfügbar einordnen. Aktualisierung mit täglicher Frische und gezieltem Refresh konkretisieren. Zugangsvoraussetzungen und Startablauf dokumentieren.

### 2. Ziele und Empfehlungen speichern

Persistenten Beratungskontext und passende MCP-Werkzeuge implementieren. Speicherung, Wiederaufnahme in einem neuen Gespräch, Korrekturen und Fehlerverhalten prüfen. Kein anfänglicher Fragebogen; unbekannte Angaben bleiben unbekannt.

### 3. Quellenbasierte Beratung

Beratungsanweisungen für Hauptziel plus zwei Alternativen, dynamische Rückfragen, begründete Umwege und konkrete Ausbaupläne erstellen. Quellenregeln, Free-to-play, gesperrte Charaktere und Übergangsteams einschließen. Aktuelle Quellenfähigkeit nachweisen oder ihre Einschränkung offenlegen.

### 4. An echten Spielfragen prüfen

Mit dem Nutzer die vier Ausgangsfragen durchspielen: nächstes Upgrade, DD8-Einstieg, langfristige Charaktere und neuestes Team. Prüfen, ob die Antworten zum Roster passen, die Ziele sinnvoll unterscheiden und Level sowie Ausrüstungsstufen und Prioritäten enthalten. Zusätzlich fehlenden Event-Status, veralteten Snapshot, widersprüchliche Quellen und gespeicherten Fortschritt in einem neuen Gespräch prüfen. Technische Tests mit synthetischen Daten; persönliche Ergebnisse bleiben lokal.

## Spätere Erweiterungen

Erreichbarkeit bei ausgeschaltetem Mac, weitere MCP-Clients und mehrere Spieler separat planen. Die erste Version baut keine eigene Chat-Oberfläche, keinen öffentlichen Hosting-Dienst und kein automatisiertes Gameplay.
