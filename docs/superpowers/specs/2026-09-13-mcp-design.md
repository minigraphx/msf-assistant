# Personal MSF MCP interface

The approved personal assistant scope is completed by a stdio MCP server built on the official MCP Python SDK 2.2. Existing OAuth, keychain and snapshot commands remain the only credential boundary. No separate chat UI or LLM calls.

The server provides status, player profile, searchable/paginated roster, searchable/paginated inventory, game character lookup and explicit snapshot refresh. Reads use local snapshots and always expose retrieval time and age; they do not silently claim live data. Catalogue lists return compact summaries; single-character lookup retains full abilities. Sync can include character data, validates the assembled snapshot, and atomically replaces the personal snapshot plus catalogue only on success. The existing snapshot format stays readable. Refreshes serialize across processes in this checkout to protect rotating tokens. Tool callers cannot select file paths, URLs, credentials or accounts.

Use stdio locally and with OpenAI Secure MCP Tunnel for private ChatGPT access. There is no public HTTP listener. Tunnel creation and account authorization are separate user-account setup; provide tested local launch configuration and precise current setup instructions. No OpenAI model API calls are introduced.

Verify synthetic service tests, actual MCP client/protocol tests (including subprocess stdio), existing OAuth tests, and a bounded live sync/check using the already authorized account. Keep personal files ignored; commit and push the complete implementation and update PR #2.
