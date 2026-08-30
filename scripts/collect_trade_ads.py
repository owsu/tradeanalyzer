from __future__ import annotations

import argparse
import json
import time

from clients.rolimons import MarketDataError, RolimonsClient, RolimonsRateLimitError
from config import (
    DATABASE_PATH,
    INVENTORY_REQUEST_TIMEOUT,
    ROLIMONS_TRADE_AD_ASSET_PRIORITY_SECONDS,
    ROLIMONS_TRADE_AD_ERROR_RETRY_SECONDS,
    ROLIMONS_TRADE_AD_POLL_SECONDS,
    ROLIMONS_TRADE_AD_REPROMOTE_SECONDS,
    ROLIMONS_TRADE_AD_ADMISSION_WINDOW_SECONDS,
    ROLIMONS_TRADE_AD_ADMISSION_MIN_ADS,
    ROLIMONS_TRADE_AD_ADMISSION_MIN_OFFER_VALUE,
    ROLIMONS_TRADE_AD_ADMISSION_MAX_USERS,
    ROLIMONS_TRADE_AD_ASSET_MIN_OFFERS,
    ROLIMONS_TRADE_AD_MAX_PRIORITY_ASSETS,
)
from database import Database
from market.trade_ad_collector import TradeAdCollector


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect permissioned Rolimon's trade ads")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    collector = TradeAdCollector(
        Database(DATABASE_PATH),
        RolimonsClient(auto_refresh=False, timeout=INVENTORY_REQUEST_TIMEOUT),
        repromote_seconds=ROLIMONS_TRADE_AD_REPROMOTE_SECONDS,
        asset_priority_seconds=ROLIMONS_TRADE_AD_ASSET_PRIORITY_SECONDS,
        admission_window_seconds=ROLIMONS_TRADE_AD_ADMISSION_WINDOW_SECONDS,
        admission_min_ads=ROLIMONS_TRADE_AD_ADMISSION_MIN_ADS,
        admission_min_offer_value=ROLIMONS_TRADE_AD_ADMISSION_MIN_OFFER_VALUE,
        admission_max_users=ROLIMONS_TRADE_AD_ADMISSION_MAX_USERS,
        asset_min_offers=ROLIMONS_TRADE_AD_ASSET_MIN_OFFERS,
        asset_max_priority=ROLIMONS_TRADE_AD_MAX_PRIORITY_ASSETS,
    )
    while True:
        delay = ROLIMONS_TRADE_AD_POLL_SECONDS
        try:
            print(json.dumps(collector.collect(), indent=2), flush=True)
        except RolimonsRateLimitError as exc:
            delay = max(exc.retry_after or ROLIMONS_TRADE_AD_ERROR_RETRY_SECONDS, delay)
            print(json.dumps({"error": str(exc), "retry_after_seconds": delay}), flush=True)
        except MarketDataError as exc:
            delay = max(ROLIMONS_TRADE_AD_ERROR_RETRY_SECONDS, delay)
            print(json.dumps({"error": str(exc), "retry_after_seconds": delay}), flush=True)
        if args.once:
            return
        time.sleep(max(delay, 5))


if __name__ == "__main__":
    main()
