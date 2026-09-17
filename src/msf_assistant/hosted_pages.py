"""Public legal pages (German). Operator details are configured, never invented.

The texts describe what the hosted service actually does; keep them in step with
hosted_web/hosted_oauth/hosted_backup when retention or data flows change.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

LAST_UPDATED = "17. September 2026"


@dataclass(frozen=True)
class Operator:
    """Natural or legal person responsible for a public instance."""

    name: str
    address: str
    email: str

    @classmethod
    def from_env(cls, env) -> Operator:
        values = {
            key: (env.get(f"MSF_OPERATOR_{key.upper()}") or "").strip()
            for key in ("name", "address", "email")
        }
        if not all(values.values()) or "@" not in values["email"]:
            raise ValueError("Public pages require operator name, address and email")
        return cls(**values)


def _operator_block(operator: Operator | None, role: str) -> str:
    if operator is None:
        return (
            f"<p><strong>{role}:</strong> Betreiberangaben sind auf dieser Instanz nicht "
            "konfiguriert.</p>"
        )
    return (
        f"<p><strong>{role}:</strong><br>{escape(operator.name)}<br>"
        f"{escape(operator.address)}<br>E-Mail: {escape(operator.email)}</p>"
    )


def privacy_body(operator: Operator | None, public_url: str) -> str:
    host = escape(public_url)
    return (
        "<h1>Datenschutzerklärung</h1>"
        f"<p>Stand: {LAST_UPDATED}. Diese Erklärung gilt für den unter {host} betriebenen "
        "MSF Assistant.</p>"
        "<h2>1. Verantwortlicher</h2>"
        + _operator_block(operator, "Verantwortlich für die Datenverarbeitung")
        + "<p>Für Fragen zum Datenschutz, Auskunftsersuchen oder Beschwerden wende dich per "
        "E-Mail an diese Adresse.</p>"
        "<h2>2. Was der Dienst tut</h2>"
        "<p>Der Dienst stellt deine eigenen Marvel-Strike-Force-Spieldaten (MSF) und einen "
        "von dir gepflegten Beratungskontext deinem KI-Assistenten (ChatGPT oder Claude) "
        "über eine geschützte MCP-Schnittstelle bereit. Er ist ein privates, nicht "
        "kommerzielles Angebot ohne Verbindung zu Scopely, Marvel, OpenAI oder Anthropic.</p>"
        "<h2>3. Welche Daten verarbeitet werden</h2>"
        "<ul>"
        "<li><strong>Verifizierte MSF-Kennung</strong> (Aussteller und Subject aus der "
        "MSF-Anmeldung), um dein Konto wiederzuerkennen. Passwörter werden nie übertragen; "
        "die Anmeldung findet bei MSF statt.</li>"
        "<li><strong>MSF-Zugangsdaten</strong> (Access- und Refresh-Token), verschlüsselt "
        "gespeichert, damit der Dienst deine Spieldaten in deinem Auftrag abrufen kann. Sie "
        "werden nie an verbundene Clients weitergegeben.</li>"
        "<li><strong>Spieldaten-Abzug</strong> aus der MSF-API: Profil, Roster, Inventar "
        "und Kataloginformationen, jeweils nur für dein eigenes Konto.</li>"
        "<li><strong>Beratungskontext</strong>: Ziele, Angaben und Empfehlungen, die du "
        "oder dein Assistent in deinem Auftrag speichern.</li>"
        "<li><strong>Verbindungen</strong>: Registrierungen und Berechtigungen der von dir "
        "verbundenen Clients (ChatGPT, Claude) mit Umfang, Zeitpunkt und Widerruf.</li>"
        "<li><strong>Browsersitzung</strong>: ein technisch notwendiges Sitzungscookie "
        "(<code>__Host-msf_session</code>, Secure, HttpOnly, bis zu 8 Stunden). Es gibt "
        "kein Tracking, keine Analyse-Werkzeuge und keine Werbung.</li>"
        "<li><strong>Technische Protokolle</strong> des Webservers: IP-Adresse, Zeitpunkt, "
        "aufgerufener Pfad ohne Abfrageparameter und Statuscode; automatische Löschung nach "
        "spätestens 14 Tagen. Der Dienst selbst protokolliert keine Inhalte oder "
        "Zugangsdaten.</li>"
        "</ul>"
        "<h2>4. Zwecke und Rechtsgrundlagen</h2>"
        "<p>Die Verarbeitung dient ausschließlich der Bereitstellung des von dir angeforderten "
        "Dienstes: Abruf deiner Spieldaten, Speicherung deines Kontexts und Zugriff durch die "
        "von dir freigegebenen Clients. Rechtsgrundlagen sind deine Einwilligung durch die "
        "MSF-Anmeldung und die ausdrückliche Freigabe jeder Verbindung (Art. 6 Abs. 1 lit. a "
        "DSGVO) sowie die Erbringung des Dienstes (Art. 6 Abs. 1 lit. b DSGVO); technische "
        "Protokolle beruhen auf dem berechtigten Interesse an Sicherheit und Missbrauchsschutz "
        "(Art. 6 Abs. 1 lit. f DSGVO). Für Personen in der Schweiz gilt das revidierte "
        "Datenschutzgesetz (DSG) entsprechend.</p>"
        "<h2>5. Empfänger und Übermittlungen</h2>"
        "<ul>"
        "<li><strong>Scopely (MSF-API)</strong>: Der Dienst ruft mit deiner Berechtigung "
        "deine eigenen Spieldaten ab. Es gelten die Bedingungen von MSF.</li>"
        "<li><strong>Dein KI-Client</strong> (OpenAI ChatGPT bzw. Anthropic Claude): Die "
        "Ausgaben der Werkzeuge, die dein Assistent aufruft, werden an den von dir "
        "verbundenen Client übertragen und dort nach dessen Bedingungen verarbeitet, "
        "gegebenenfalls in den USA. Diese Übermittlung löst du selbst aus; der Widerruf einer "
        "Verbindung löscht keine Daten, die der Client bereits erhalten hat.</li>"
        "<li><strong>Hosting</strong>: Die Daten liegen auf einem vom Verantwortlichen "
        "verwalteten Server in Frankfurt am Main (Deutschland). Es findet kein Verkauf und "
        "keine Weitergabe zu Werbezwecken statt.</li>"
        "</ul>"
        "<h2>6. Speicherdauer</h2>"
        "<p>Konto, Zugangsdaten, Spieldaten und Kontext bleiben gespeichert, bis du dein "
        "Konto löschst. Zugriffstoken verbundener Clients laufen nach 15 Minuten ab, "
        "Verlängerungstoken nach spätestens 30 Tagen oder mit dem Widerruf. Browsersitzungen "
        "enden nach 8 Stunden. Sicherungskopien werden für den Wiederherstellungsfall "
        "aufbewahrt und laufen nach der Aufbewahrungsfrist von höchstens 30 Sicherungen "
        "zeitversetzt aus; eine Löschung wirkt dort erst mit dem Ablauf der jeweiligen "
        "Kopie.</p>"
        "<h2>7. Deine Rechte</h2>"
        "<p>Du kannst jederzeit unter <a href=\"/account\">Konto und Verbindungen</a> "
        "einzelne Verbindungen widerrufen oder dein Konto vollständig löschen. Die Löschung "
        "deaktiviert den Zugang und entfernt aktive Zugangsdaten und Spielerdateien; "
        "Widerrufs- und Sicherheitseinträge können ohne Personenbezug verbleiben. Darüber "
        "hinaus hast du das Recht auf Auskunft, Berichtigung, Löschung, Einschränkung der "
        "Verarbeitung, Datenübertragbarkeit und Widerspruch sowie das Recht, eine erteilte "
        "Einwilligung zu widerrufen. Wende dich dafür an die oben genannte E-Mail-Adresse. "
        "Du kannst dich außerdem bei einer Datenschutz-Aufsichtsbehörde beschweren, in der "
        "Schweiz beim Eidgenössischen Datenschutz- und Öffentlichkeitsbeauftragten "
        "(EDÖB).</p>"
        "<h2>8. Sicherheit</h2>"
        "<p>Die Verbindung ist TLS-verschlüsselt. MSF-Zugangsdaten werden verschlüsselt "
        "gespeichert; Codes und Token verbundener Clients werden nur als Prüfsummen "
        "abgelegt. Jeder Zugriff wird der angemeldeten Spielerin oder dem angemeldeten "
        "Spieler zugeordnet; fremde Daten sind nicht erreichbar.</p>"
        "<h2>9. Änderungen</h2>"
        "<p>Änderungen dieser Erklärung werden hier mit neuem Stand veröffentlicht.</p>"
    )


def terms_body(operator: Operator | None, public_url: str) -> str:
    host = escape(public_url)
    return (
        "<h1>Nutzungsbedingungen</h1>"
        f"<p>Stand: {LAST_UPDATED}. Diese Bedingungen gelten für die Nutzung des MSF "
        f"Assistant unter {host}.</p>"
        "<h2>1. Anbieter und Charakter des Dienstes</h2>"
        + _operator_block(operator, "Anbieter")
        + "<p>Der Dienst ist ein privates, nicht kommerzielles Hobbyangebot. Er steht in "
        "keiner Verbindung zu Scopely, Marvel, OpenAI oder Anthropic. „Marvel Strike Force“ "
        "und „MSF“ sind Marken ihrer jeweiligen Inhaber.</p>"
        "<h2>2. Leistung</h2>"
        "<p>Der Dienst stellt deine eigenen MSF-Spieldaten und deinen gespeicherten "
        "Beratungskontext über eine MCP-Schnittstelle dem von dir verbundenen KI-Assistenten "
        "bereit. Empfehlungen entstehen im Assistenten und sind KI-generiert; sie werden ohne "
        "Gewähr für Richtigkeit, Aktualität oder Spielerfolg bereitgestellt.</p>"
        "<h2>3. Nutzungsregeln</h2>"
        "<ul>"
        "<li>Du verbindest ausschließlich dein eigenes MSF-Konto und hältst die "
        "Nutzungsbedingungen von MSF ein.</li>"
        "<li>Du musst mindestens 16 Jahre alt sein und die Altersanforderungen von MSF "
        "erfüllen.</li>"
        "<li>Du umgehst keine Zugriffs- oder Ratenbegrenzungen, führst keine automatisierten "
        "Massenabfragen durch und störst den Betrieb nicht.</li>"
        "<li>Du versuchst nicht, auf Daten anderer Spielerinnen und Spieler zuzugreifen.</li>"
        "</ul>"
        "<h2>4. Verfügbarkeit und Änderungen</h2>"
        "<p>Es besteht kein Anspruch auf Verfügbarkeit. Der Dienst kann jederzeit gewartet, "
        "geändert, eingeschränkt oder eingestellt werden. Nach einer Wiederherstellung aus "
        "einer Sicherung können Daten auf einen älteren Stand zurückfallen und Verbindungen "
        "müssen neu freigegeben werden.</p>"
        "<h2>5. Haftung</h2>"
        "<p>Der Dienst wird unentgeltlich und „wie besehen“ bereitgestellt. Soweit gesetzlich "
        "zulässig ist jede Haftung ausgeschlossen, insbesondere für Spielentscheidungen, die "
        "auf Empfehlungen des Assistenten beruhen, für Datenverlust und für Ausfälle. "
        "Unberührt bleibt die Haftung für Vorsatz und grobe Fahrlässigkeit sowie zwingende "
        "gesetzliche Haftung.</p>"
        "<h2>6. Sperrung und Beendigung</h2>"
        "<p>Bei Verstößen gegen diese Bedingungen oder bei Gefährdung des Betriebs kann der "
        "Anbieter Verbindungen widerrufen oder Konten sperren. Du kannst dein Konto jederzeit "
        "unter <a href=\"/account\">Konto und Verbindungen</a> löschen.</p>"
        "<h2>7. Datenschutz</h2>"
        "<p>Die Verarbeitung deiner Daten ist in der <a href=\"/privacy.html\">"
        "Datenschutzerklärung</a> beschrieben.</p>"
        "<h2>8. Anwendbares Recht</h2>"
        "<p>Es gilt Schweizer Recht unter Ausschluss der Kollisionsnormen. Gerichtsstand ist "
        "der Sitz des Anbieters, soweit zwingende Verbraucherschutzvorschriften an deinem "
        "Wohnsitz nichts anderes vorsehen.</p>"
        "<h2>9. Änderungen dieser Bedingungen</h2>"
        "<p>Änderungen werden hier mit neuem Stand veröffentlicht; die weitere Nutzung nach "
        "der Veröffentlichung gilt als Zustimmung.</p>"
    )
