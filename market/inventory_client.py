from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time

import requests


class InventoryError(RuntimeError):
    """Raised when a public collectible inventory cannot be read."""


class InventoryPrivateError(InventoryError):
    """Raised when a user has hidden their inventory."""


class InventoryRateLimitError(InventoryError):
    """Raised when Roblox asks the caller to pause before making more requests."""

    def __init__(self, retry_after: float | None = None, *, operation: str = "Owner") -> None:
        self.retry_after = retry_after
        message = f"{operation} request was rate limited (HTTP 429)"
        if retry_after is not None:
            message += f"; retry after {retry_after:g} seconds"
        super().__init__(message)


class AssetOwnersUnavailableError(InventoryError):
    """Raised when Roblox forbids owner enumeration for a specific asset."""


@dataclass(frozen=True)
class CollectibleInstance:
    uaid: int
    asset_id: int
    name: str
    rap: int


@dataclass(frozen=True)
class AssetOwnerInstance:
    uaid: int
    asset_id: int
    owner_id: int | None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class OwnerPage:
    owners: tuple[AssetOwnerInstance, ...]
    next_cursor: str | None


class RobloxInventoryClient:
    BASE_URL = "https://inventory.roblox.com"

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: float = 15.0,
        security_cookie: str | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.session.headers.update(
            {"Accept": "application/json", "User-Agent": "RobloxTraderPrototype/1.0"}
        )
        if security_cookie:
            self.session.cookies.set(".ROBLOSECURITY", security_cookie.strip())

    def resolve_usernames(self, usernames: list[str]) -> dict[str, int]:
        cleaned = list(dict.fromkeys(name.strip() for name in usernames if name.strip()))
        if not cleaned:
            return {}
        try:
            response = self.session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": cleaned, "excludeBannedUsers": False},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise InventoryError(f"Username lookup failed: {exc}") from exc
        if response.status_code == 429:
            raise InventoryRateLimitError(operation="Username lookup")
        if not response.ok:
            raise InventoryError(f"Username lookup failed with HTTP {response.status_code}")
        try:
            data = response.json().get("data", [])
        except (ValueError, AttributeError) as exc:
            raise InventoryError("Username lookup returned invalid JSON") from exc
        return {
            str(row["requestedUsername"]).casefold(): int(row["id"])
            for row in data if row.get("id") and row.get("requestedUsername")
        }

    def asset_owners_page(
        self,
        asset_id: int,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> OwnerPage:
        asset_id = int(asset_id)
        if asset_id <= 0:
            raise ValueError("asset_id must be a positive integer")
        if limit not in {10, 25, 50, 100}:
            raise ValueError("limit must be one of: 10, 25, 50, 100")
        params = {"sortOrder": "Asc", "limit": limit}
        if cursor:
            params["cursor"] = cursor
        try:
            response = self.session.get(
                f"{self.BASE_URL}/v2/assets/{asset_id}/owners",
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise InventoryError(f"Owner request failed: {exc}") from exc
        if response.status_code == 429:
            retry_after = None
            try:
                retry_after = max(float(response.headers.get("Retry-After", "")), 0.0)
            except (TypeError, ValueError):
                pass
            raise InventoryRateLimitError(retry_after)
        if response.status_code == 403:
            raise AssetOwnersUnavailableError(
                f"Owner list is unavailable for asset {asset_id} (HTTP 403)"
            )
        if not response.ok:
            raise InventoryError(
                f"Owner request failed with HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise InventoryError("Owner endpoint returned invalid JSON") from exc

        owners: list[AssetOwnerInstance] = []
        for raw in payload.get("data", []):
            owner = raw.get("owner")
            owner_id = None
            if isinstance(owner, dict) and owner.get("id") is not None:
                owner_id = int(owner["id"])
            updated_at = None
            if raw.get("updated"):
                try:
                    updated_at = datetime.fromisoformat(
                        str(raw["updated"]).replace("Z", "+00:00")
                    )
                except ValueError:
                    updated_at = None
            owners.append(
                AssetOwnerInstance(
                    uaid=int(raw["id"]),
                    asset_id=asset_id,
                    owner_id=owner_id,
                    updated_at=updated_at,
                )
            )
        return OwnerPage(tuple(owners), payload.get("nextPageCursor"))

    def premium_status(self, user_id: int) -> bool | None:
        """Return Premium status when authenticated; unknown otherwise."""
        if not self.session.cookies.get(".ROBLOSECURITY"):
            return None
        try:
            response = self.session.get(
                f"https://premiumfeatures.roblox.com/v1/users/{int(user_id)}/validate-membership",
                timeout=self.timeout,
            )
        except requests.RequestException:
            return None
        if not response.ok:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if isinstance(payload, bool):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("isPremium"), bool):
            return payload["isPremium"]
        return None

    def collectible_inventory(self, user_id: int) -> list[CollectibleInstance]:
        user_id = int(user_id)
        if user_id <= 0:
            raise ValueError("user_id must be a positive integer")

        cursor: str | None = None
        results: list[CollectibleInstance] = []
        while True:
            params = {"sortOrder": "Asc", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            try:
                response = self.session.get(
                    f"{self.BASE_URL}/v1/users/{user_id}/assets/collectibles",
                    params=params,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise InventoryError(f"Inventory request failed: {exc}") from exc

            if response.status_code in {401, 403}:
                raise InventoryPrivateError(f"Inventory for user {user_id} is private")
            if response.status_code == 429:
                retry_after = None
                try:
                    retry_after = max(float(response.headers.get("Retry-After", "")), 0.0)
                except (TypeError, ValueError):
                    pass
                raise InventoryRateLimitError(retry_after, operation="Inventory")
            if not response.ok:
                raise InventoryError(
                    f"Inventory request failed with HTTP {response.status_code}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise InventoryError("Inventory returned invalid JSON") from exc

            for raw in payload.get("data", []):
                try:
                    results.append(
                        CollectibleInstance(
                            uaid=int(raw["userAssetId"]),
                            asset_id=int(raw["assetId"]),
                            name=str(raw.get("name") or ""),
                            rap=max(int(raw.get("recentAveragePrice") or 0), 0),
                        )
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise InventoryError(
                        f"Unexpected collectible inventory item: {raw!r}"
                    ) from exc

            cursor = payload.get("nextPageCursor")
            if not cursor:
                break
        return results


class HybridRobloxInventoryClient(RobloxInventoryClient):
    """Prefer Open Cloud inventories while retaining legacy owner APIs."""

    OPEN_CLOUD_URL = "https://apis.roblox.com/cloud/v2/users/{user_id}/inventory-items"

    def __init__(self, *, open_cloud_api_key: str | None = None,
                 open_cloud_enabled: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self.open_cloud_api_key = (open_cloud_api_key or "").strip()
        self.open_cloud_enabled = bool(open_cloud_enabled and self.open_cloud_api_key)
        self.open_cloud_retry_at = 0.0
        self.last_inventory_source = "legacy"

    def _open_cloud_collectible_inventory(
        self, user_id: int
    ) -> list[CollectibleInstance]:
        page_token: str | None = None
        results: list[CollectibleInstance] = []
        while True:
            params = {
                "maxPageSize": 100,
                "filter": "onlyCollectibles=true;inventoryItemAssetTypes=*",
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                response = self.session.get(
                    self.OPEN_CLOUD_URL.format(user_id=int(user_id)),
                    params=params,
                    headers={"x-api-key": self.open_cloud_api_key},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise InventoryError(f"Open Cloud inventory request failed: {exc}") from exc
            if response.status_code == 429:
                retry_after = None
                try:
                    retry_after = max(float(response.headers.get("Retry-After", "")), 0.0)
                except (TypeError, ValueError):
                    pass
                raise InventoryRateLimitError(
                    retry_after, operation="Open Cloud inventory"
                )
            if response.status_code in {401, 403}:
                raise InventoryError(
                    f"Open Cloud inventory request failed with HTTP {response.status_code}"
                )
            if not response.ok:
                raise InventoryError(
                    f"Open Cloud inventory request failed with HTTP {response.status_code}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise InventoryError("Open Cloud inventory returned invalid JSON") from exc
            raw_items = payload.get("inventoryItems", [])
            if not isinstance(raw_items, list):
                raise InventoryError("Open Cloud inventory returned malformed items")
            if not raw_items and page_token:
                break
            for raw in raw_items:
                details = raw.get("assetDetails") if isinstance(raw, dict) else None
                if not isinstance(details, dict):
                    continue
                try:
                    results.append(CollectibleInstance(
                        uaid=int(details["instanceId"]),
                        asset_id=int(details["assetId"]),
                        name=str(details.get("displayName") or details.get("name") or ""),
                        rap=0,
                    ))
                except (KeyError, TypeError, ValueError) as exc:
                    raise InventoryError(
                        f"Unexpected Open Cloud inventory item: {raw!r}"
                    ) from exc
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return results

    def collectible_inventory(self, user_id: int) -> list[CollectibleInstance]:
        if self.open_cloud_enabled and time.monotonic() >= self.open_cloud_retry_at:
            try:
                items = self._open_cloud_collectible_inventory(user_id)
                self.last_inventory_source = "open_cloud"
                return items
            except InventoryRateLimitError as exc:
                self.open_cloud_retry_at = time.monotonic() + max(
                    float(exc.retry_after or 300), 60
                )
            except InventoryError:
                self.open_cloud_retry_at = time.monotonic() + 60
                # Keep collection alive while Open Cloud is unavailable or the
                # key is misconfigured, without doubling every user request.
                # Legacy failures retain their existing private/rate-limit
                # handling in the caller.
                self.last_inventory_source = "legacy_fallback"
                return super().collectible_inventory(user_id)
        self.last_inventory_source = (
            "legacy_fallback" if self.open_cloud_enabled else "legacy"
        )
        return super().collectible_inventory(user_id)
