from __future__ import annotations

import os

from dotenv import load_dotenv


load_dotenv()

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
UPGRADE_BONUS = 8.0
DOWNGRADE_EXPECTED_OP_PENALTY = 4.0
ITEM_SIZE_WEIGHT = 8.0
PROJECTED_RECEIVE_PENALTY = 25.0
PROJECTED_GIVE_BONUS = 10.0
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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")

# ---------------------------------------------------------------------------
# Discord proof ingestion
# ---------------------------------------------------------------------------
PROOF_CHANNEL_ID = int(os.getenv("PROOF_CHANNEL_ID", "535250426061258753"))
PROOF_BACKFILL_LIMIT = int(os.getenv("PROOF_BACKFILL_LIMIT", "5"))
PROOF_PARSER_COOLDOWN = float(os.getenv("PROOF_PARSER_COOLDOWN", "2"))
