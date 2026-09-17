# Data coverage of the advice

Checked on 14 September 2026 against the client and the field names of a local
snapshot. No personal values are published here.

| Area | Available in the assistant | Limit |
| --- | --- | --- |
| Profile | level, total power, strongest team, collection counters | no full game history |
| Roster | character id, level, gearTier, stars, abilities, ISO, power | power proves neither suitability for every mode nor an event completion |
| Inventory | item and quantity | does not cap upgrade planning in this version |
| Catalog | names, traits, abilities, unlock stars, status | no current meta rating; no proof of a free unlock path |
| Advisor context | stored player facts, goals, recommendations and sources | read with their provenance, not as additional API evidence |
| Events / Dark Dimension | not in the snapshot | progress stays unknown until a reliable statement exists |

## What the API describes in addition

The [official API specification](https://developer.marvelstrikeforce.com/beta/msf-api.json)
(beta 0.2.1, checked on 14 September 2026) describes `/player/v1/events` and
`/player/v1/events/{eventId}` with the additional scope `m3p.f.pr.act`. They
return qualifying events with progress; `Objective.progress` may contain
completed tiers, points and repetitions, and for raids it denotes alliance
progress. `/game/v1/events` returns general event information.

The current client does not query these routes and does not request the
additional scope at login. A complete, permanent archive of DD/Legendary
completions is therefore not proven. A missing event row would not be evidence
of "not completed".

## Consequence for the advice

Unknown completions are asked for when they matter and stored as a player fact
with a timestamp. Existing statements may be corrected. A matching roster,
ownership of a character or high power does not replace this evidence. Ask
specifically about contradictory statements.

## Freshness

`get_status` reports the retrieval state and a separate catalog timestamp. When
data is missing or older than 24 hours the connected assistant should call
`refresh_data` once, provided the tool is available and the host allows the
action. Disclose failures; then work with the marked old state or ask for the
missing information. An explicitly requested refresh is possible at any time.
No automatic background service is set up. In read-only mode a local `sync` is
required; in the hosted service, game data expires after 30 days and must be
refreshed.
