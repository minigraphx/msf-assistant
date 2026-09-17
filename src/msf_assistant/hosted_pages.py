"""Public legal pages (English). Operator details are configured, never invented.

The texts describe what the hosted service actually does; keep them in step with
hosted_web/hosted_oauth/hosted_backup when retention or data flows change.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

LAST_UPDATED = "17 September 2026"


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
        return f"<p><strong>{role}:</strong> operator details are not configured.</p>"
    return (
        f"<p><strong>{role}:</strong><br>{escape(operator.name)}<br>"
        f"{escape(operator.address)}<br>Email: {escape(operator.email)}</p>"
    )


def privacy_body(operator: Operator | None, public_url: str) -> str:
    host = escape(public_url)
    return (
        "<h1>Privacy Notice</h1>"
        f"<p>Last updated: {LAST_UPDATED}. This notice applies to the Strike Advisor operated "
        f"at {host}.</p>"
        "<h2>1. Controller</h2>"
        + _operator_block(operator, "Responsible for data processing")
        + "<p>For privacy questions, access requests or complaints, contact this email "
        "address.</p>"
        "<h2>2. What the service does</h2>"
        "<p>The service makes your own Marvel Strike Force (MSF) game data and an advisor "
        "context you maintain available to your AI assistant (ChatGPT or Claude) through a "
        "protected MCP interface. The game data is provided by Scopely's Marvel Strike Force "
        "API under Scopely's API Terms of Use; it is provided to you “as is”, without any "
        "guarantee of availability, accuracy or completeness. This service is a private, "
        "non-commercial service that is not endorsed by, sponsored by or affiliated with "
        "Scopely, Marvel, OpenAI or Anthropic.</p>"
        "<h2>3. Data we process</h2>"
        "<ul>"
        "<li><strong>Verified MSF identity</strong> (issuer and subject from the MSF "
        "sign-in) to recognise your account. Passwords are never transmitted to us; the "
        "sign-in happens at MSF.</li>"
        "<li><strong>MSF credentials</strong> (access and refresh tokens), stored encrypted, "
        "so the service can fetch your game data on your behalf. They are never passed to "
        "connected clients.</li>"
        "<li><strong>Game data snapshot</strong> from the MSF API: profile, roster, inventory "
        "and catalog information, only for your own account. It is kept for at most 30 days "
        "and is then deleted automatically; run a refresh to fetch it again.</li>"
        "<li><strong>Advisor context</strong>: goals, facts and recommendations that you or "
        "your assistant store on your behalf.</li>"
        "<li><strong>Connections</strong>: registrations and grants of the clients you "
        "connect (ChatGPT, Claude) with scope, time and revocation.</li>"
        "<li><strong>Browser session</strong>: one strictly necessary session cookie "
        "(<code>__Host-msf_session</code>, Secure, HttpOnly, up to 8 hours). There is no "
        "tracking, no analytics and no advertising.</li>"
        "<li><strong>Technical web server logs</strong>: IP address, time, requested path "
        "without query parameters and status code; deleted automatically after at most "
        "14 days. The service itself never logs content or credentials.</li>"
        "</ul>"
        "<h2>4. Purposes and legal bases</h2>"
        "<p>Processing serves solely to provide the service you requested: fetching your "
        "game data, storing your context and giving access to the clients you approved. "
        "The legal bases are your consent through the MSF sign-in and the explicit approval "
        "of each connection (Art. 6(1)(a) GDPR) and the performance of the service "
        "(Art. 6(1)(b) GDPR); technical logs rest on the legitimate interest in security "
        "and abuse prevention (Art. 6(1)(f) GDPR). For persons in Switzerland the revised "
        "Federal Act on Data Protection (FADP) applies accordingly.</p>"
        "<h2>5. Recipients and transfers</h2>"
        "<ul>"
        "<li><strong>Scopely (MSF API)</strong>: the service fetches your own game data with "
        "your authorisation. MSF's terms apply.</li>"
        "<li><strong>Your AI client</strong> (OpenAI ChatGPT or Anthropic Claude): the "
        "output of the tools your assistant calls is sent to the client you connected and "
        "processed there under its own terms, possibly in the USA. You trigger this transfer "
        "yourself; revoking a connection does not delete data the client already "
        "received.</li>"
        "<li><strong>Hosting</strong>: data is stored on a server managed by the controller "
        "in Frankfurt am Main, Germany. Data is never sold or shared for advertising.</li>"
        "</ul>"
        "<h2>6. Retention</h2>"
        "<p>Your account, credentials and advisor context are kept until you delete your "
        "account. Game data fetched from the MSF API is kept for at most 30 days after each "
        "refresh and is deleted immediately when you delete your account. Access tokens of "
        "connected clients expire after 15 minutes, refresh tokens after at most 30 days or "
        "on revocation. Browser sessions end after 8 hours. Backups for disaster recovery "
        "contain your account record, encrypted credentials and advisor context but never "
        "game data; they are deleted after at most 30 days, so a deletion takes effect there "
        "when the respective copy expires.</p>"
        "<h2>7. Your rights</h2>"
        "<p>At any time you can revoke individual connections or delete your account under "
        '<a href="/account">Account and connections</a>. Deletion deactivates access and '
        "removes active credentials and player files; revocation and security records may "
        "remain without personal reference. You also have the right of access, "
        "rectification, erasure, restriction of processing, data portability and objection, "
        "and the right to withdraw consent. Contact the email address above for these. You "
        "may also complain to a data protection authority, in Switzerland the Federal Data "
        "Protection and Information Commissioner (FDPIC).</p>"
        "<h2>8. Security</h2>"
        "<p>The connection is TLS-encrypted. MSF credentials are stored encrypted; codes and "
        "tokens of connected clients are stored only as hashes. Every access is bound to the "
        "signed-in player; other players' data is not reachable.</p>"
        "<h2>9. Changes</h2>"
        "<p>Changes to this notice are published here with a new date.</p>"
    )


def terms_body(operator: Operator | None, public_url: str) -> str:
    host = escape(public_url)
    return (
        "<h1>Terms of Service</h1>"
        f"<p>Last updated: {LAST_UPDATED}. These terms govern the use of the Strike Advisor "
        f"at {host}.</p>"
        "<h2>1. Provider and nature of the service</h2>"
        + _operator_block(operator, "Provider")
        + "<p>The service is a private, non-commercial hobby project. Game data is provided "
        "by Scopely's Marvel Strike Force API and is used under Scopely's API Terms of Use. "
        "The service is not endorsed by, sponsored by or affiliated with Scopely, Marvel, "
        "OpenAI or Anthropic. “Marvel Strike Force” and “MSF” are trademarks of their "
        "respective owners.</p>"
        "<h2>2. Service</h2>"
        "<p>The service makes your own MSF game data and your stored advisor context available "
        "through an MCP interface to the AI assistant you connect. Game data is provided "
        "“as is”; Scopely may change, limit or withdraw the API at any time, which can "
        "interrupt or end this service. Recommendations are produced by the assistant and "
        "are AI-generated; they are provided without warranty of accuracy, currency or "
        "in-game success.</p>"
        "<h2>3. Rules of use</h2>"
        "<ul>"
        "<li>You connect only your own MSF account and comply with the MSF terms of "
        "service.</li>"
        "<li>You must be at least 16 years old and meet the age requirements of MSF.</li>"
        "<li>You do not circumvent access or rate limits, run automated bulk queries or "
        "disrupt the service.</li>"
        "<li>You do not attempt to access other players' data.</li>"
        "</ul>"
        "<h2>4. Availability and changes</h2>"
        "<p>There is no entitlement to availability. The service may be maintained, changed, "
        "restricted or discontinued at any time. After a restore from backup, data may revert "
        "to an older state and connections must be approved again.</p>"
        "<h2>5. Liability</h2>"
        "<p>The service is provided free of charge and “as is”. To the extent "
        "permitted by law, all liability is excluded, in particular for in-game decisions "
        "based on the assistant's recommendations, for data loss and for outages. Liability "
        "for intent and gross negligence and mandatory statutory liability remain "
        "unaffected.</p>"
        "<h2>6. Suspension and termination</h2>"
        "<p>In case of violations of these terms or threats to operation, the provider may "
        "revoke connections or suspend accounts. You can delete your account at any time "
        'under <a href="/account">Account and connections</a>.</p>'
        "<h2>7. Privacy</h2>"
        '<p>How your data is processed is described in the <a href="/privacy.html">Privacy '
        "Notice</a>.</p>"
        "<h2>8. Governing law</h2>"
        "<p>Swiss law applies, excluding its conflict-of-law rules. The place of jurisdiction "
        "is the provider's domicile, unless mandatory consumer protection rules at your place "
        "of residence provide otherwise.</p>"
        "<h2>9. Changes to these terms</h2>"
        "<p>Changes are published here with a new date; continued use after publication "
        "constitutes acceptance.</p>"
    )
