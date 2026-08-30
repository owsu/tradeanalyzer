# Roblox Trader Prototype

The project is split by responsibility so each part can grow independently without turning one file into a giant bot script.

## Layout

```text
.
├── main.py                    # Flask/API entry point
├── config.py                  # env + tunable scoring constants
├── models.py                  # shared data structures
├── clients/
│   ├── rolimons.py            # Rolimons HTTP/data access
│   └── gemini.py              # Gemini provider wrapper
├── trading/
│   ├── evaluator.py           # coordinates trade evaluation
│   ├── scoring.py             # scoring math
│   └── risk.py                # recommendation gates + reasons
├── proofs/
│   ├── parser.py              # LLM extraction prompt + schema conversion
│   ├── validator.py           # deterministic arithmetic verification
│   └── ingestion.py           # source-agnostic RawProof entry point
├── integrations/
│   └── discord_bot.py         # Discord-specific proof collection
├── scripts/
│   ├── evaluate_trade.py      # interactive trade evaluator
│   └── list_models.py         # inspect Gemini models
└── tests/
    ├── test_evaluator.py
    └── test_proofs.py
```

## Setup

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in the credentials you actually use.

## Run

Run these commands from the project root.

Interactive trade evaluator:

```bash
python -m scripts.evaluate_trade
```

Flask API:

```bash
python main.py
```

Discord bot:

```bash
python -m integrations.discord_bot
```

The bot downloads up to four JPEG, PNG, or WebP proof attachments while the
Discord message is available and sends the image bytes plus caption to Gemini.
Per-image and total byte limits are configurable with `PROOF_MAX_IMAGE_BYTES`
and `PROOF_MAX_TOTAL_IMAGE_BYTES`. Image-only proofs are supported.

Set `PROOF_BACKFILL_LIMIT=all` to walk the complete accessible channel history.
Every message is stored in SQLite under `(source, channel_id, message_id)`, so
restarts skip completed messages. Edits with changed text/images are reparsed.
Identical content posted under a new message ID keeps a separate occurrence but
reuses the existing parse result. Failed parses retry up to
`PROOF_MAX_ATTEMPTS`; processing claims older than
`PROOF_PROCESSING_TIMEOUT_SECONDS` are recovered after crashes. Inspect recent
records with `GET /proofs?limit=50`.

The global **Submit Trade Proof** message command routes a selected Discord
message through the same ingestion and deduplication pipeline. Enable **User
Install** for the application in the Discord Developer Portal, add the app to
your Discord account, then right-click a message and choose **Apps → Submit
Trade Proof**. Set `DISCORD_SUBMITTER_USER_ID` to your own Discord user ID to
prevent other authorized users from feeding the analyzer. Availability in a
third-party server still depends on that server allowing external/user-installed
apps; the command does not grant background channel access.

The proof parser defaults to the stable multimodal
`gemini-3.5-flash-lite`. Override it with `GEMINI_MODEL` if needed.

List Gemini models:

```bash
python -m scripts.list_models
```

Tests:

```bash
python -m pytest -q
```

## Database and automation controls

The app creates a SQLite database at `data/trader.db` by default. Override it
with `DATABASE_PATH`. Schema creation is automatic and automation begins in a
fail-closed state: all three pause switches are on.

Successful `POST /evaluate` results are stored in `trade_evaluations` and
returned with an `evaluation_id`. Query recent results with
`GET /evaluations?limit=50` (the limit is capped at 200).

Read the current state:

```bash
curl http://127.0.0.1:5000/automation
```

Enable only trade-ad automation (PowerShell example):

```powershell
$body = @{
  all_paused = $false
  trade_ads_paused = $false
  roblox_trades_paused = $true
  actor = "operator"
  reason = "enable ads while keeping trade mutations disabled"
} | ConvertTo-Json
Invoke-RestMethod -Method Put -Uri http://127.0.0.1:5000/automation `
  -Headers @{ "X-Automation-Token" = $env:AUTOMATION_CONTROL_TOKEN } `
  -ContentType application/json -Body $body
```

`all_paused` overrides both subsystem switches. The Rolimons client enforces
`require_trade_ads_enabled()` immediately before posting. Future workers that
accept, decline, counter, or send Roblox trades must call
`require_roblox_trades_enabled()`. Every state change is persisted in
`automation_audit` and is available from `GET /automation/history`.

Send `expected_revision` with updates to prevent a stale dashboard or worker
from overwriting a newer operator action. Configure `AUTOMATION_CONTROL_TOKEN`
before exposing the API through any proxy.

## Roblox trade client

Set `ROBLOX_SECURITY_COOKIE` and `ROBLOX_USER_ID` in `.env`. The security cookie
grants account access and must never be committed or printed. Roblox's trade
API is a legacy cookie-authenticated API, so endpoint details are isolated in
`clients/roblox.py`.

Read-only operations work while automation is paused:

- `GET /roblox/trades?status=inbound&limit=25`
- `GET /roblox/trades/{trade_id}`
- `GET /roblox/actions` for the persistent mutation audit trail

Mutations require the `X-Automation-Token` header when configured and require
both `all_paused=false` and `roblox_trades_paused=false`:

- `POST /roblox/trades/{trade_id}/accept`
- `POST /roblox/trades/{trade_id}/decline`
- `POST /roblox/trades/send`
- `POST /roblox/trades/{trade_id}/counter`

Send and counter bodies use Roblox user-asset IDs (UAIDs), not catalog asset
IDs:

```json
{
  "partner_user_id": 123456,
  "giving_user_asset_ids": [111, 222],
  "receiving_user_asset_ids": [333],
  "giving_robux": 0,
  "receiving_robux": 0
}
```

The client validates positive and unique UAIDs, the four-item-per-side limit,
non-negative Robux, and self-trades. Roblox remains authoritative for ownership,
holding periods, Robux limits, privacy, 2FA, and trade eligibility.

## Public-inventory trade inference

### Permissioned Rolimon's trade-ad discovery

When authorized to use Rolimon's bot API, run its recent-ad collector as an
independent process:

```powershell
python -m scripts.collect_trade_ads
```

Use `--once` for a single diagnostic request. The collector polls the official
recent-ad endpoint every 30 seconds by default, permanently deduplicates by ad
ID, records each advertiser and offered/requested asset, and pushes eligible
advertisers into the bounded inventory queue. A repeat advertiser is promoted
at most once per `ROLIMONS_TRADE_AD_REPROMOTE_SECONDS`, preventing frequent ads
from forcing repeated inventory requests. Offered assets receive higher sweep
priority than requested assets because the advertiser is more likely to own
them. Trade ads identify assets, not UAIDs; positive Roblox inventory and owner
observations remain required, and reciprocal movement is still mandatory before
classifying a swap.

Advertiser polling cools automatically with inactivity: 30 minutes for ads in
the last 6 hours, 6 hours through day 3, daily through day 30, and weekly through
day 365. After 365 days the user is disabled from automatic polling, while all
ads, inventory observations, and ownership history remain stored. Any new ad
immediately re-enables and promotes that user. These windows and intervals are
configurable with the `TRADE_AD_*` environment settings.

Add public users and run one collection/correlation cycle:

```powershell
python -m scripts.infer_trades --add-user 123 --add-user 456 --once
```

Initialize or refresh the full Rolimons limited catalog and begin incremental
owner sweeps:

```powershell
python -m scripts.infer_trades --sync-catalog
```

Refresh value, demand, and trend metadata without starting owner requests:

```powershell
python -m scripts.infer_trades --sync-catalog-only
```

Active proof/transfer users are polled first through a persistent, bounded
queue. Each cycle polls at most `WATCHED_USER_POLL_BUDGET` due users, spaces
requests by `WATCHED_USER_REQUEST_DELAY_SECONDS`, and schedules successful
users for a later refresh. A priority boost is consumed after one attempt so
the remaining users continue rotating fairly. Owner pages then use two durable
lanes: six pages for proof/transfer-priority assets and four pages for a fair
background baseline by default. Each request advances exactly one saved cursor,
then rotates to another due asset so high-value, many-page items cannot starve
the catalog. Configure the lanes with `OWNER_SWEEP_PRIORITY_PAGE_BUDGET` and
`OWNER_SWEEP_BACKGROUND_PAGE_BUDGET`.

Owner-page requests are spaced by `OWNER_SWEEP_REQUEST_DELAY_SECONDS` (default
5 seconds). A Roblox HTTP 429 starts a shared, persistent exponential cooldown
for both user and owner requests and honors a longer `Retry-After` value.
An asset-specific HTTP 403 disables that asset's sweep because Roblox is not
exposing its owner list (this occurs for some legacy faces migrated to bundles).
These expected conditions appear as `skipped_assets` and `rate_limited` in the
cycle output; `errors` is reserved for unexpected request failures.

Omit `--once` to poll continuously. The collector records positive UAID
ownership observations; missing or private inventories never imply transfers.
A transfer becomes an inferred trade only when one or more UAIDs move A to B
and one or more UAIDs move B to A within `INFERRED_TRADE_WINDOW_SECONDS`.
One-way ownership movement remains unmatched because it may be a marketplace
sale. Both sides are limited to four inferred items, and every inferred result
keeps `possible_robux=true` because inventory changes cannot reveal Robux.

Owner pages are checkpointed per asset, so each cycle resumes its cursor rather
than restarting a large item. Public owners are discovered across the catalog;
private owners returned as `null` are counted but never identified or inferred.
Long runs of hidden-only pages are backed off for a day instead of consuming
every cycle. Proof assets and observed-transfer assets are promoted dynamically,
and proof sender/receiver usernames that Roblox resolves exactly become watched
users. Owner `updated` timestamps are used for transfer correlation when present.
Discovered owners remain dormant until an ownership transfer activates fast
user-level polling. When a Roblox cookie is configured, active-user polls also
record Premium status when Roblox exposes it. `TRADE_HOLD_DAYS` and
`SALE_HOLD_DAYS` are scheduling assumptions (defaults 3 and 7), not evidence
used to manufacture a swap; reciprocal UAID movement remains mandatory.

### Proof-linked market values

Parsed proof items now include a Roblox asset ID when it is visible. Missing
IDs are resolved only when the item name has one exact, unique match in the
synchronized catalog. After either proof ingestion or trade inference, the
reconciler links a proof when its giving/receiving asset multisets exactly match
one—and only one—inferred reciprocal swap within
`PROOF_TRADE_LINK_WINDOW_DAYS`. It never links on value or fuzzy name alone.

Valid proofs retain displayed values as low-confidence context. When a proof
also identifies a unique target item, overpay/underpay direction and amount,
the system treats repeated overpay as value above the Rolimons baseline and
repeated underpay as value below it.
Raw overpay is normalized for payment quality before it becomes an implied
premium. A largest payment item worth at least 85% of the target requires no
fragmentation compensation; 65-85%, 40-65%, and below 40% use progressively
larger compensation tiers. More than two payment items and lower value-weighted
demand add further compensation, capped at 15% of baseline value.
Linked ownership swaps raise the evidence confidence. With enough direct
observations, the estimator uses their recency-weighted median; before then it
borrows adjustment ratios from items between half and twice the target's
Rolimons value, discounted by value distance. Estimates include an uncertainty
band and direct/peer sample counts. Manual `ESTIMATED_VALUES` remain the
highest-priority override, and duplicate proof content does not add evidence.

Inspect all estimates that currently meet the evidence threshold with:

```powershell
python -m scripts.show_market_values
```

After upgrading estimator logic, rebuild compatible evidence already stored in
SQLite without another model call:

```powershell
python -m scripts.rebuild_market_evidence
```

The same report is available from `GET /market-values` when the Flask API is
running.

View catalog, ownership, trade, proof, and market-evidence counts with:

```powershell
python -m scripts.market_status
```

The status includes completed, in-progress, and untouched baseline assets,
active priority assets, hidden-owner backoffs, and any global cooldown.

The same summary is available from `GET /market-status`.

Parser schema version 5 separates RAP from assigned/trading value and extracts
the target, direction, and signed overpay/underpay adjustment. RAP is never used
to validate a deal or train learned values. To migrate accessible historical
proofs, set `PROOF_PARSER_VERSION=5`, temporarily set
`PROOF_BACKFILL_LIMIT=all`, and restart the Discord bot. Inference output lists
new matches under `proof_trade_links`; evaluated items identify learned values
with a source such as `implied market (direct implied trades, 4 proofs)`.

## Demand scoring

Rolimons often leaves RAP-only items without an assigned demand score. The
evaluator treats those items as normal-demand (`2`) instead of erasing the
entire demand difference. Each trade side includes `demand_coverage`, the
value-weighted share backed by an actual Rolimons rating, and the reasons call
out whenever the neutral fallback was used.

Upgrade scoring is cost-sensitive: underpay/equal upgrades receive the strongest
direction incentive, low overpays retain most of it, moderate/high overpays lose
it progressively, and extreme overpays become a direction penalty. A receiving
side with well-covered terrible demand cannot auto-accept regardless of score.

Demand is weighted by each item's effective-value share. For example, an 80k
demand-4 item plus a 5k demand-0 item has weighted demand `3.76`, rather than an
unweighted average of `2`. Projected and rare risk penalties use the same
value-share principle. Effective value itself remains an additive trade total.

Downgrade direction scoring uses largest-item retention. A downgrade whose main
received item retains at least 85% of the given main item's value gets a strong
concentration bonus; 65-85% gets a smaller bonus, 40-65% is mildly penalized,
and below 40% is treated as heavy fragmentation. Total overpay and demand are
still scored independently.

## Import examples

New code should import the implementation directly from its owning module:

```python
from clients.rolimons import RolimonsClient
from trading.evaluator import TradeEvaluator
from proofs.parser import parse_proof
from proofs.validator import validate_proof
```

There are intentionally no compatibility wrapper modules at the project root.
