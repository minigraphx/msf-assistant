# MSF server application credentials

The registered personal MSF application supplies a client ID and client secret.
The current client instead requires a user-provided API key and never sends a
client secret when exchanging or refreshing tokens. Correct this credential
handling without adding a login server or changing resource methods.

## Evidence

- The [official API specification](https://developer.marvelstrikeforce.com/beta/msf-api.json)
  documents a shared `x-api-key` value alongside Bearer authentication. It is
  public configuration, not a personal credential.
- The [OAuth server metadata](https://hydra-public.prod.m3.scopelypv.com/.well-known/openid-configuration),
  checked on 2026-09-13, advertises `client_secret_basic` and `client_secret_post`.
  Use Basic authentication for the server application. A real login is still
  needed to verify the application's registered method and permissions.

## Design

Keep the existing synchronous requests-based client. Load the client secret
from `MSF_CLIENT_SECRET` and require it with the client ID when loading the
server configuration from `.env`. Use the documented public API key by default;
retain `MSF_API_KEY` as an optional override, treating empty values as unset.

Keep existing direct `Settings(client_id, api_key, ...)` construction compatible
by adding an optional secret field at the end. Token operations must reject a
missing secret before making an HTTP request. Hide the secret from settings
representations, never add it to authorization URLs or resource headers, and
send it only through Basic authentication on token requests. Encode credentials
as OAuth form components before Basic encoding. Disable redirects on token POSTs.

Update setup instructions and the environment example. Ignore local dotenv
backups and editor swap files while keeping `.env.example` tracked. Do not read,
commit, or push the user's `.env` or swap files.

## Validation and limits

Test configuration from environment and a temporary dotenv file, missing
credentials, default/overridden API keys, prepared HTTP token requests for both
grants, secret isolation, and HTTP failures. Run all pytest tests and Ruff under
Python 3.12. This verifies request construction with synthetic credentials; it
does not establish a live connection to the user's account. Browser login,
token storage, the privacy page, and MCP remain separate steps.
