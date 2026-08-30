from __future__ import annotations

from datetime import UTC, datetime
import time

from database import Database
from market.inventory_client import (
    AssetOwnersUnavailableError,
    InventoryError,
    InventoryRateLimitError,
    RobloxInventoryClient,
)


class OwnerSweeper:
    """Incrementally sweep owner pages without bypassing private inventories."""

    def __init__(
        self,
        database: Database,
        client: RobloxInventoryClient,
        *,
        request_delay_seconds: float = 0.0,
        page_rotation_seconds: float = 60.0,
        hidden_page_threshold: int = 20,
        hidden_retry_seconds: float = 86400,
    ) -> None:
        self.database = database
        self.client = client
        self.request_delay_seconds = max(float(request_delay_seconds), 0.0)
        self.page_rotation_seconds = max(float(page_rotation_seconds), 0.0)
        self.hidden_page_threshold = max(int(hidden_page_threshold), 1)
        self.hidden_retry_seconds = max(float(hidden_retry_seconds), 1.0)

    def sweep_due_pages(self, page_budget: int, *, lane: str = "any") -> dict:
        pages = 0
        visible_owners = 0
        hidden_serials = 0
        transfers: list[int] = []
        errors: list[dict] = []
        skipped_assets: list[dict] = []
        rate_limited: dict | None = None
        cooldown = self.database.scheduler_cooldown()
        if cooldown:
            return {
                "lane": lane, "pages": 0, "visible_owners": 0,
                "hidden_serials": 0, "transfer_ids": [], "skipped_assets": [],
                "rate_limited": None, "cooldown": cooldown, "errors": [],
            }

        while pages < page_budget:
            if pages and self.request_delay_seconds:
                time.sleep(self.request_delay_seconds)
            target = self.database.next_owner_sweep_target(lane)
            if target is None:
                break
            observed_at = datetime.now(UTC)
            try:
                page = self.client.asset_owners_page(
                    target["asset_id"], cursor=target["cursor"]
                )
            except InventoryRateLimitError as exc:
                retry_seconds = max(exc.retry_after or 300.0, 60.0)
                self.database.fail_owner_sweep_page(
                    target["asset_id"], str(exc), retry_seconds=retry_seconds
                )
                cooldown = self.database.record_scheduler_rate_limit(exc.retry_after)
                rate_limited = {
                    "asset_id": target["asset_id"],
                    "retry_after_seconds": retry_seconds,
                    "message": str(exc),
                }
                pages += 1
                # A 429 applies to the caller, not merely this asset. Continuing
                # would fail and reschedule every remaining due item.
                break
            except AssetOwnersUnavailableError as exc:
                self.database.disable_owner_sweep_asset(
                    target["asset_id"], str(exc)
                )
                skipped_assets.append(
                    {"asset_id": target["asset_id"], "reason": str(exc)}
                )
                pages += 1
                continue
            except InventoryError as exc:
                self.database.fail_owner_sweep_page(target["asset_id"], str(exc))
                errors.append({"asset_id": target["asset_id"], "error": str(exc)})
                pages += 1
                continue

            public = [owner for owner in page.owners if owner.owner_id is not None]
            hidden_serials += len(page.owners) - len(public)
            visible_owners += len(public)
            transfers.extend(
                self.database.observe_owner_page(
                    target["asset_id"], public, observed_at
                )
            )
            self.database.complete_owner_sweep_page(
                target["asset_id"],
                next_cursor=page.next_cursor,
                observed_at=observed_at,
                visible_owner_count=len(public),
                page_rotation_seconds=self.page_rotation_seconds,
                hidden_page_threshold=self.hidden_page_threshold,
                hidden_retry_seconds=self.hidden_retry_seconds,
            )
            self.database.record_scheduler_success()
            pages += 1

        return {
            "lane": lane,
            "pages": pages,
            "visible_owners": visible_owners,
            "hidden_serials": hidden_serials,
            "transfer_ids": transfers,
            "skipped_assets": skipped_assets,
            "rate_limited": rate_limited,
            "cooldown": cooldown,
            "errors": errors,
        }

    def sweep_lanes(self, *, priority_budget: int, background_budget: int) -> dict:
        priority = self.sweep_due_pages(priority_budget, lane="priority")
        if priority.get("rate_limited") or priority.get("cooldown"):
            background = self.sweep_due_pages(0, lane="background")
        else:
            background = self.sweep_due_pages(background_budget, lane="background")
        return {"priority": priority, "background": background}
