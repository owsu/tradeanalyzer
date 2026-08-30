from __future__ import annotations

import os

from dotenv import load_dotenv


load_dotenv()


def _optional_positive_int_env(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer or left blank") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer or left blank")
    return value


# ---------------------------------------------------------------------------
# Persistence and automation safety
# ---------------------------------------------------------------------------
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/trader.db")
# When set, state-changing automation endpoints require this value in the
# X-Automation-Token header. The API only binds to localhost by default, but a
# token should still be configured before placing it behind a proxy.
AUTOMATION_CONTROL_TOKEN = os.getenv("AUTOMATION_CONTROL_TOKEN")
ROBLOX_SECURITY_COOKIE = os.getenv("ROBLOX_SECURITY_COOKIE")
ROBLOX_USER_ID = _optional_positive_int_env("ROBLOX_USER_ID")

# ---------------------------------------------------------------------------
# Public-inventory trade inference
# ---------------------------------------------------------------------------
INVENTORY_POLL_INTERVAL_SECONDS = int(
    os.getenv("INVENTORY_POLL_INTERVAL_SECONDS", "300")
)
WATCHED_USER_POLL_BUDGET = int(os.getenv("WATCHED_USER_POLL_BUDGET", "3"))
WATCHED_USER_REQUEST_DELAY_SECONDS = float(
    os.getenv("WATCHED_USER_REQUEST_DELAY_SECONDS", "10")
)
WATCHED_USER_POLL_INTERVAL_SECONDS = int(
    os.getenv("WATCHED_USER_POLL_INTERVAL_SECONDS", "1800")
)
WATCHED_USER_FAILURE_RETRY_SECONDS = int(
    os.getenv("WATCHED_USER_FAILURE_RETRY_SECONDS", "3600")
)
TRADE_AD_HOT_WINDOW_SECONDS = int(os.getenv("TRADE_AD_HOT_WINDOW_SECONDS", "21600"))
TRADE_AD_HOT_POLL_SECONDS = int(os.getenv("TRADE_AD_HOT_POLL_SECONDS", "1800"))
TRADE_AD_WARM_WINDOW_SECONDS = int(os.getenv("TRADE_AD_WARM_WINDOW_SECONDS", "259200"))
TRADE_AD_WARM_POLL_SECONDS = int(os.getenv("TRADE_AD_WARM_POLL_SECONDS", "21600"))
TRADE_AD_ACTIVE_WINDOW_SECONDS = int(os.getenv("TRADE_AD_ACTIVE_WINDOW_SECONDS", "2592000"))
TRADE_AD_ACTIVE_POLL_SECONDS = int(os.getenv("TRADE_AD_ACTIVE_POLL_SECONDS", "86400"))
TRADE_AD_COLD_POLL_SECONDS = int(os.getenv("TRADE_AD_COLD_POLL_SECONDS", "604800"))
TRADE_AD_ARCHIVE_AFTER_DAYS = int(os.getenv("TRADE_AD_ARCHIVE_AFTER_DAYS", "365"))
ROLIMONS_TRADE_AD_POLL_SECONDS = float(
    os.getenv("ROLIMONS_TRADE_AD_POLL_SECONDS", "30")
)
ROLIMONS_TRADE_AD_REPROMOTE_SECONDS = int(
    os.getenv("ROLIMONS_TRADE_AD_REPROMOTE_SECONDS", "1800")
)
ROLIMONS_TRADE_AD_ASSET_PRIORITY_SECONDS = int(
    os.getenv("ROLIMONS_TRADE_AD_ASSET_PRIORITY_SECONDS", "21600")
)
ROLIMONS_TRADE_AD_ERROR_RETRY_SECONDS = float(
    os.getenv("ROLIMONS_TRADE_AD_ERROR_RETRY_SECONDS", "120")
)
INFERRED_TRADE_WINDOW_SECONDS = int(
    os.getenv("INFERRED_TRADE_WINDOW_SECONDS", "600")
)
INVENTORY_REQUEST_TIMEOUT = float(os.getenv("INVENTORY_REQUEST_TIMEOUT", "15"))
OWNER_SWEEP_PAGE_BUDGET = int(os.getenv("OWNER_SWEEP_PAGE_BUDGET", "10"))
OWNER_SWEEP_PRIORITY_PAGE_BUDGET = int(
    os.getenv("OWNER_SWEEP_PRIORITY_PAGE_BUDGET", "6")
)
OWNER_SWEEP_BACKGROUND_PAGE_BUDGET = int(
    os.getenv("OWNER_SWEEP_BACKGROUND_PAGE_BUDGET", "4")
)
OWNER_SWEEP_PAGE_ROTATION_SECONDS = float(
    os.getenv("OWNER_SWEEP_PAGE_ROTATION_SECONDS", "60")
)
OWNER_SWEEP_HIDDEN_PAGE_THRESHOLD = int(
    os.getenv("OWNER_SWEEP_HIDDEN_PAGE_THRESHOLD", "20")
)
OWNER_SWEEP_HIDDEN_RETRY_SECONDS = float(
    os.getenv("OWNER_SWEEP_HIDDEN_RETRY_SECONDS", "86400")
)
OWNER_SWEEP_REQUEST_DELAY_SECONDS = float(
    os.getenv("OWNER_SWEEP_REQUEST_DELAY_SECONDS", "5")
)
OWNER_SWEEP_HIGH_VALUE_INTERVAL_SECONDS = int(
    os.getenv("OWNER_SWEEP_HIGH_VALUE_INTERVAL_SECONDS", "600")
)
OWNER_SWEEP_NORMAL_INTERVAL_SECONDS = int(
    os.getenv("OWNER_SWEEP_NORMAL_INTERVAL_SECONDS", "3600")
)
TRADE_HOLD_DAYS = int(os.getenv("TRADE_HOLD_DAYS", "3"))
SALE_HOLD_DAYS = int(os.getenv("SALE_HOLD_DAYS", "7"))
PROOF_TRADE_LINK_WINDOW_DAYS = int(os.getenv("PROOF_TRADE_LINK_WINDOW_DAYS", "7"))
LEARNED_VALUE_MIN_PROOFS = int(os.getenv("LEARNED_VALUE_MIN_PROOFS", "3"))
LEARNED_VALUE_MAX_AGE_DAYS = int(os.getenv("LEARNED_VALUE_MAX_AGE_DAYS", "90"))

# ---------------------------------------------------------------------------
# Trading/scoring configuration
# ---------------------------------------------------------------------------
# These are intentionally visible starter heuristics. Tune them as you collect
# paper-trading results; they are not meant to be permanent market truths.
BASE_SCORE = 50.0
ACCEPT_THRESHOLD = 65.0
DECLINE_THRESHOLD = 40.0

VALUE_SCORE_WEIGHT = 0.60
DEMAND_POINT_WEIGHT = 4.0
# Rolimons commonly leaves RAP-only items unrated (-1). Treating that as no
# difference discards useful demand information from the rated side. Normal is
# the least opinionated baseline, while demand_coverage exposes the uncertainty.
UNRATED_DEMAND_BASELINE = 2
# Upgrade incentive is cost-sensitive. An underpay/equal upgrade is usually a
# portfolio-quality improvement; increasingly large overpays burn that benefit.
UPGRADE_UNDERPAY_BONUS = 15.0
UPGRADE_LOW_OP_MAX_PCT = 3.0
UPGRADE_LOW_OP_BONUS = 12.0
UPGRADE_MODERATE_OP_MAX_PCT = 7.5
UPGRADE_MODERATE_OP_BONUS = 7.0
UPGRADE_HIGH_OP_MAX_PCT = 12.0
UPGRADE_HIGH_OP_BONUS = 2.0
UPGRADE_EXCESS_OP_PENALTY_PER_PCT = 1.5
UPGRADE_EXCESS_OP_MAX_PENALTY = 15.0
# Downgrades should distinguish a near-size main item plus adds from heavy
# fragmentation into small items. Effective-value gain is scored separately.
DOWNGRADE_NEAR_SIZE_MIN_RATIO = 0.85
DOWNGRADE_NEAR_SIZE_BONUS = 15.0
DOWNGRADE_SOLID_SIZE_MIN_RATIO = 0.65
DOWNGRADE_SOLID_SIZE_BONUS = 7.0
DOWNGRADE_FRAGMENTED_MIN_RATIO = 0.40
DOWNGRADE_FRAGMENTED_PENALTY = 2.0
DOWNGRADE_HEAVY_FRAGMENTATION_PENALTY = 8.0
ITEM_SIZE_WEIGHT = 8.0
PROJECTED_RECEIVE_PENALTY = 25.0
PROJECTED_GIVE_BONUS = 10.0
PROJECTED_REVIEW_SHARE_THRESHOLD = 0.10
RARE_RECEIVE_UNCERTAINTY_PENALTY = 4.0

# Temporary stand-in for the market-value database you plan to build later.
ESTIMATED_VALUES: dict[int, int] = {
    583721561: 14999,
    10159622004: 13200,
    16477149823: 9500,
    19027209: 6600,
    46357082: 41000,
}

DEMAND_NAMES = {
    -1: "none",
    0: "terrible",
    1: "low",
    2: "normal",
    3: "high",
    4: "amazing",
}

ROLIMONS_ITEM_ENDPOINTS = (
    "https://api.rolimons.com/items/v2/itemdetails",
    "https://api.rolimons.com/items/v1/itemdetails",
    "https://www.rolimons.com/itemapi/itemdetails",
)

# ---------------------------------------------------------------------------
# Gemini proof parser
# ---------------------------------------------------------------------------
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# ---------------------------------------------------------------------------
# Discord proof ingestion
# ---------------------------------------------------------------------------
PROOF_CHANNEL_ID = int(os.getenv("PROOF_CHANNEL_ID", "535250426061258753"))
DISCORD_SUBMITTER_USER_ID = _optional_positive_int_env(
    "DISCORD_SUBMITTER_USER_ID"
)
_proof_backfill_raw = os.getenv("PROOF_BACKFILL_LIMIT", "5").strip().lower()
PROOF_BACKFILL_LIMIT: int | None = (
    None if _proof_backfill_raw == "all" else int(_proof_backfill_raw)
)
PROOF_PARSER_COOLDOWN = float(os.getenv("PROOF_PARSER_COOLDOWN", "2"))
PROOF_QUEUE_SIZE = int(os.getenv("PROOF_QUEUE_SIZE", "25"))
PROOF_MAX_ATTEMPTS = int(os.getenv("PROOF_MAX_ATTEMPTS", "3"))
PROOF_PROCESSING_TIMEOUT_SECONDS = int(
    os.getenv("PROOF_PROCESSING_TIMEOUT_SECONDS", "600")
)
PROOF_PARSER_VERSION = os.getenv("PROOF_PARSER_VERSION", "5")
PROOF_MAX_IMAGES = int(os.getenv("PROOF_MAX_IMAGES", "4"))
PROOF_MAX_IMAGE_BYTES = int(os.getenv("PROOF_MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))
PROOF_MAX_TOTAL_IMAGE_BYTES = int(
    os.getenv("PROOF_MAX_TOTAL_IMAGE_BYTES", str(15 * 1024 * 1024))
)
