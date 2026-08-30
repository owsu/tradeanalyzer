from __future__ import annotations

import json

from config import (
    DATABASE_PATH,
    LEARNED_VALUE_MAX_AGE_DAYS,
    LEARNED_VALUE_MIN_PROOFS,
    LEARNED_VALUE_RECENCY_HALF_LIFE_DAYS,
    LEARNED_VALUE_PROOF_EXECUTABILITY_WEIGHT,
    LEARNED_VALUE_VERIFIED_EXECUTABILITY_WEIGHT,
)
from database import Database


def main() -> None:
    items = Database(DATABASE_PATH).learned_market_values(
        min_proofs=LEARNED_VALUE_MIN_PROOFS,
        max_age_days=LEARNED_VALUE_MAX_AGE_DAYS,
        recency_half_life_days=LEARNED_VALUE_RECENCY_HALF_LIFE_DAYS,
        proof_executability_weight=LEARNED_VALUE_PROOF_EXECUTABILITY_WEIGHT,
        verified_executability_weight=LEARNED_VALUE_VERIFIED_EXECUTABILITY_WEIGHT,
    )
    print(
        json.dumps(
            {
                "minimum_distinct_proofs": LEARNED_VALUE_MIN_PROOFS,
                "maximum_age_days": LEARNED_VALUE_MAX_AGE_DAYS,
                "recency_half_life_days": LEARNED_VALUE_RECENCY_HALF_LIFE_DAYS,
                "proof_executability_weight": LEARNED_VALUE_PROOF_EXECUTABILITY_WEIGHT,
                "verified_executability_weight": LEARNED_VALUE_VERIFIED_EXECUTABILITY_WEIGHT,
                "item_count": len(items),
                "items": items,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
