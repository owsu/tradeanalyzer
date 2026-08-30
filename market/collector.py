from __future__ import annotations

from datetime import UTC, datetime

from database import Database
from market.inventory_client import InventoryError, InventoryRateLimitError, RobloxInventoryClient


class InventoryCollector:
    def __init__(self, database: Database, client: RobloxInventoryClient) -> None:
        self.database = database
        self.client = client

    def poll_user(self, user_id: int) -> dict:
        observed_at = datetime.now(UTC)
        try:
            items = self.client.collectible_inventory(user_id)
        except InventoryError as exc:
            self.database.record_inventory_failure(user_id, str(exc), observed_at)
            if isinstance(exc, InventoryRateLimitError):
                self.database.record_scheduler_rate_limit(exc.retry_after)
            raise
        transfer_ids = self.database.observe_inventory(user_id, items, observed_at)
        premium = self.client.premium_status(user_id)
        if premium is not None:
            self.database.update_premium_status(user_id, premium)
        return {
            "user_id": int(user_id),
            "item_count": len(items),
            "transfer_ids": transfer_ids,
            "premium": premium,
            "observed_at": observed_at.isoformat(),
        }

    def poll_watched_users(self) -> list[dict]:
        results: list[dict] = []
        for user_id in self.database.watched_user_ids():
            if self.database.scheduler_cooldown():
                results.append({"user_id": user_id, "skipped": "global cooldown"})
                break
            try:
                results.append(self.poll_user(user_id))
            except InventoryError as exc:
                results.append({"user_id": user_id, "error": str(exc)})
        return results
