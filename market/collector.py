from __future__ import annotations

from datetime import UTC, datetime
import time

from database import Database
from market.inventory_client import InventoryError, InventoryRateLimitError, RobloxInventoryClient


class InventoryCollector:
    def __init__(
        self, database: Database, client: RobloxInventoryClient, *,
        poll_budget: int = 3, request_delay_seconds: float = 10,
        user_poll_interval_seconds: float = 1800,
        failure_retry_seconds: float = 3600,
        trade_ad_hot_window_seconds: int = 21600,
        trade_ad_hot_poll_seconds: int = 1800,
        trade_ad_warm_window_seconds: int = 259200,
        trade_ad_warm_poll_seconds: int = 21600,
        trade_ad_active_window_seconds: int = 2592000,
        trade_ad_active_poll_seconds: int = 86400,
        trade_ad_cold_poll_seconds: int = 604800,
        trade_ad_archive_after_days: int = 365,
    ) -> None:
        self.database = database
        self.client = client
        self.poll_budget = max(int(poll_budget), 0)
        self.request_delay_seconds = max(float(request_delay_seconds), 0)
        self.user_poll_interval_seconds = max(float(user_poll_interval_seconds), 1)
        self.failure_retry_seconds = max(float(failure_retry_seconds), 1)
        self.trade_ad_hot_window_seconds = max(int(trade_ad_hot_window_seconds), 1)
        self.trade_ad_hot_poll_seconds = max(int(trade_ad_hot_poll_seconds), 1)
        self.trade_ad_warm_window_seconds = max(int(trade_ad_warm_window_seconds), self.trade_ad_hot_window_seconds)
        self.trade_ad_warm_poll_seconds = max(int(trade_ad_warm_poll_seconds), 1)
        self.trade_ad_active_window_seconds = max(int(trade_ad_active_window_seconds), self.trade_ad_warm_window_seconds)
        self.trade_ad_active_poll_seconds = max(int(trade_ad_active_poll_seconds), 1)
        self.trade_ad_cold_poll_seconds = max(int(trade_ad_cold_poll_seconds), 1)
        self.trade_ad_archive_after_days = max(int(trade_ad_archive_after_days), 1)

    def poll_user(self, user_id: int) -> dict:
        observed_at = datetime.now(UTC)
        try:
            items = self.client.collectible_inventory(user_id)
        except InventoryError as exc:
            self.database.record_inventory_failure(user_id, str(exc), observed_at)
            if isinstance(exc, InventoryRateLimitError):
                cooldown = self.database.record_scheduler_rate_limit(exc.retry_after)
                self.database.schedule_next_user_poll(
                    user_id, observed_at,
                    delay_seconds=cooldown["remaining_seconds"], succeeded=False,
                )
            else:
                self.database.schedule_next_user_poll(
                    user_id, observed_at,
                    delay_seconds=self.failure_retry_seconds, succeeded=False,
                )
            raise
        transfer_ids, unchanged = self.database.observe_inventory_snapshot(
            user_id, items, observed_at
        )
        premium = self.client.premium_status(user_id)
        if premium is not None:
            self.database.update_premium_status(user_id, premium)
        next_delay = self.database.recommended_watched_user_delay(
            user_id, default_seconds=int(self.user_poll_interval_seconds),
            hot_window_seconds=self.trade_ad_hot_window_seconds,
            hot_interval_seconds=self.trade_ad_hot_poll_seconds,
            warm_window_seconds=self.trade_ad_warm_window_seconds,
            warm_interval_seconds=self.trade_ad_warm_poll_seconds,
            active_window_seconds=self.trade_ad_active_window_seconds,
            active_interval_seconds=self.trade_ad_active_poll_seconds,
            cold_interval_seconds=self.trade_ad_cold_poll_seconds,
        )
        self.database.schedule_next_user_poll(
            user_id, observed_at,
            delay_seconds=next_delay, succeeded=True,
        )
        self.database.record_scheduler_success()
        return {
            "user_id": int(user_id),
            "item_count": len(items),
            "transfer_ids": transfer_ids,
            "unchanged": unchanged,
            "inventory_source": getattr(self.client, "last_inventory_source", "legacy"),
            "premium": premium,
            "observed_at": observed_at.isoformat(),
        }

    def poll_watched_users(self) -> list[dict]:
        results: list[dict] = []
        self.database.archive_inactive_trade_ad_users(
            self.trade_ad_archive_after_days
        )
        due_users = self.database.due_watched_user_ids(self.poll_budget)
        for index, user_id in enumerate(due_users):
            if self.database.scheduler_cooldown():
                results.append({"user_id": user_id, "skipped": "global cooldown"})
                break
            if index and self.request_delay_seconds:
                time.sleep(self.request_delay_seconds)
            try:
                results.append(self.poll_user(user_id))
            except InventoryError as exc:
                results.append({"user_id": user_id, "error": str(exc)})
                if isinstance(exc, InventoryRateLimitError):
                    break
        return results
