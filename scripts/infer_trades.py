from __future__ import annotations

import argparse
import json
import time

from config import (
    DATABASE_PATH,
    INFERRED_TRADE_WINDOW_SECONDS,
    INVENTORY_POLL_INTERVAL_SECONDS,
    INVENTORY_REQUEST_TIMEOUT,
    OWNER_SWEEP_HIGH_VALUE_INTERVAL_SECONDS,
    OWNER_SWEEP_NORMAL_INTERVAL_SECONDS,
    OWNER_SWEEP_PRIORITY_PAGE_BUDGET,
    OWNER_SWEEP_BACKGROUND_PAGE_BUDGET,
    OWNER_SWEEP_PAGE_ROTATION_SECONDS,
    OWNER_SWEEP_HIDDEN_PAGE_THRESHOLD,
    OWNER_SWEEP_HIDDEN_RETRY_SECONDS,
    OWNER_SWEEP_REQUEST_DELAY_SECONDS,
    PROOF_TRADE_LINK_WINDOW_DAYS,
    ROBLOX_SECURITY_COOKIE,
    ROBLOX_OPEN_CLOUD_API_KEY,
    ROBLOX_OPEN_CLOUD_INVENTORY_ENABLED,
    WATCHED_USER_POLL_BUDGET,
    WATCHED_USER_REQUEST_DELAY_SECONDS,
    WATCHED_USER_POLL_INTERVAL_SECONDS,
    WATCHED_USER_FAILURE_RETRY_SECONDS,
    TRADE_AD_HOT_WINDOW_SECONDS,
    TRADE_AD_HOT_POLL_SECONDS,
    TRADE_AD_WARM_WINDOW_SECONDS,
    TRADE_AD_WARM_POLL_SECONDS,
    TRADE_AD_ACTIVE_WINDOW_SECONDS,
    TRADE_AD_ACTIVE_POLL_SECONDS,
    TRADE_AD_COLD_POLL_SECONDS,
    TRADE_AD_ARCHIVE_AFTER_DAYS,
)
from clients.rolimons import RolimonsClient
from database import Database
from market.collector import InventoryCollector
from market.correlator import TradeCorrelator
from market.inventory_client import HybridRobloxInventoryClient
from market.owner_sweeper import OwnerSweeper


def sync_limited_catalog(database: Database) -> int:
    market = RolimonsClient()
    for asset_id, raw in market.items.items():
        rap = max(int(raw[2] or 0), 0)
        assigned = int(raw[3] or 0)
        value = assigned if assigned > 0 else rap
        interval = (
            OWNER_SWEEP_HIGH_VALUE_INTERVAL_SECONDS
            if value >= 100_000
            else OWNER_SWEEP_NORMAL_INTERVAL_SECONDS
        )
        database.upsert_tracked_asset(
            int(asset_id),
            name=str(raw[0]),
            market_value=value,
            interval_seconds=interval,
            demand_score=int(raw[5] if raw[5] is not None else -1),
            trend_score=int(raw[6] if raw[6] is not None else -1),
        )
    return len(market.items)


def run_cycle(database: Database) -> dict:
    inventory_client = HybridRobloxInventoryClient(
        timeout=INVENTORY_REQUEST_TIMEOUT,
        security_cookie=ROBLOX_SECURITY_COOKIE,
        open_cloud_api_key=ROBLOX_OPEN_CLOUD_API_KEY,
        open_cloud_enabled=ROBLOX_OPEN_CLOUD_INVENTORY_ENABLED,
    )
    collector = InventoryCollector(
        database,
        inventory_client,
        poll_budget=WATCHED_USER_POLL_BUDGET,
        request_delay_seconds=WATCHED_USER_REQUEST_DELAY_SECONDS,
        user_poll_interval_seconds=WATCHED_USER_POLL_INTERVAL_SECONDS,
        failure_retry_seconds=WATCHED_USER_FAILURE_RETRY_SECONDS,
        trade_ad_hot_window_seconds=TRADE_AD_HOT_WINDOW_SECONDS,
        trade_ad_hot_poll_seconds=TRADE_AD_HOT_POLL_SECONDS,
        trade_ad_warm_window_seconds=TRADE_AD_WARM_WINDOW_SECONDS,
        trade_ad_warm_poll_seconds=TRADE_AD_WARM_POLL_SECONDS,
        trade_ad_active_window_seconds=TRADE_AD_ACTIVE_WINDOW_SECONDS,
        trade_ad_active_poll_seconds=TRADE_AD_ACTIVE_POLL_SECONDS,
        trade_ad_cold_poll_seconds=TRADE_AD_COLD_POLL_SECONDS,
        trade_ad_archive_after_days=TRADE_AD_ARCHIVE_AFTER_DAYS,
    )
    polls = collector.poll_watched_users()
    owner_sweep = OwnerSweeper(
        database,
        inventory_client,
        request_delay_seconds=OWNER_SWEEP_REQUEST_DELAY_SECONDS,
        page_rotation_seconds=OWNER_SWEEP_PAGE_ROTATION_SECONDS,
        hidden_page_threshold=OWNER_SWEEP_HIDDEN_PAGE_THRESHOLD,
        hidden_retry_seconds=OWNER_SWEEP_HIDDEN_RETRY_SECONDS,
    ).sweep_lanes(
        priority_budget=OWNER_SWEEP_PRIORITY_PAGE_BUDGET,
        background_budget=OWNER_SWEEP_BACKGROUND_PAGE_BUDGET,
    )
    inferred_ids = TradeCorrelator(
        database, window_seconds=INFERRED_TRADE_WINDOW_SECONDS
    ).correlate()
    proof_links = database.reconcile_proofs_with_inferred_trades(
        PROOF_TRADE_LINK_WINDOW_DAYS
    )
    return {
        "owner_sweep": owner_sweep,
        "active_user_polls": polls,
        "inferred_trade_ids": inferred_ids,
        "proof_trade_links": proof_links,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll public inventories")
    parser.add_argument("--add-user", type=int, action="append", default=[])
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--sync-catalog",
        action="store_true",
        help="refresh the tracked limited catalog from Rolimons",
    )
    parser.add_argument(
        "--sync-catalog-only",
        action="store_true",
        help="refresh catalog value/demand/trend data without running a sweep",
    )
    args = parser.parse_args()

    database = Database(DATABASE_PATH)
    for user_id in args.add_user:
        database.add_watched_user(user_id, source="cli")
    if args.sync_catalog or args.sync_catalog_only:
        print(f"Tracked {sync_limited_catalog(database)} Rolimons items")
    if args.sync_catalog_only:
        return

    while True:
        print(json.dumps(run_cycle(database), indent=2))
        if args.once:
            return
        time.sleep(INVENTORY_POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
