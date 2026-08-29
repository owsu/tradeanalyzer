from __future__ import annotations

from typing import Sequence

import requests

from config import DEMAND_NAMES, ROLIMONS_ITEM_ENDPOINTS
from models import ItemSnapshot


class MarketDataError(RuntimeError):
    """Raised when market data cannot be fetched or understood."""


class ItemNotFoundError(KeyError):
    """Raised when an asset ID is not present in the current catalog."""


class RolimonsClient:
    """Rolimons client with an explicit refresh boundary."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: float = 10.0,
        auto_refresh: bool = True,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.items: dict[str, Sequence] = {}
        self.last_endpoint: str | None = None

        if auto_refresh:
            self.refresh()

    def refresh(self) -> None:
        errors: list[str] = []

        for url in ROLIMONS_ITEM_ENDPOINTS:
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                payload = response.json()
                items = payload.get("items")
                if not isinstance(items, dict):
                    raise MarketDataError("response did not contain an 'items' object")

                self.items = items
                self.last_endpoint = url
                return
            except (requests.RequestException, ValueError, MarketDataError) as exc:
                errors.append(f"{url}: {exc}")

        raise MarketDataError(
            "Unable to fetch Rolimons item data. Attempts:\n" + "\n".join(errors)
        )

    @staticmethod
    def _optional_positive_int(value: object) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _int_or_default(value: object, default: int = 0) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed >= 0 else default

    def get_item(self, asset_id: int) -> ItemSnapshot:
        raw = self.items.get(str(int(asset_id)))
        if raw is None:
            raise ItemNotFoundError(f"Item {asset_id} is not in the Rolimons catalog")
        if len(raw) < 10:
            raise MarketDataError(
                f"Unexpected Rolimons item format for {asset_id}: expected >=10 fields"
            )

        # itemdetails layout:
        # 0 name, 1 acronym, 2 RAP, 3 assigned Value, 4 default value,
        # 5 demand, 6 trend, 7 projected, 8 hyped, 9 rare.
        rap = self._int_or_default(raw[2])
        roli_value = self._optional_positive_int(raw[3])
        default_value = self._int_or_default(raw[4], default=rap)

        try:
            demand_score = int(raw[5])
        except (TypeError, ValueError):
            demand_score = -1
        if demand_score not in DEMAND_NAMES:
            demand_score = -1

        return ItemSnapshot(
            asset_id=int(asset_id),
            name=str(raw[0]),
            acronym=str(raw[1] or ""),
            rap=rap,
            roli_value=roli_value,
            default_value=default_value,
            demand_name=DEMAND_NAMES.get(demand_score, "none"),
            demand_score=demand_score,
            projected=(raw[7] == 1),
            rare=(raw[9] == 1),
        )

    def post_trade_ad(
        self,
        cookies: str,
        items_giving: list[int],
        items_receiving: list[int],
        request_tags: str | list[str],
        player_id: int,
    ) -> dict:
        url = "https://api.rolimons.com/tradeads/v1/createad"
        tags = (
            [tag.strip() for tag in request_tags.split(",") if tag.strip()]
            if isinstance(request_tags, str)
            else request_tags
        )
        response = self.session.post(
            url,
            cookies={"_RoliVerification": cookies},
            json={
                "player_id": player_id,
                "offer_item_ids": items_giving,
                "request_item_ids": items_receiving,
                "request_tags": tags,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    # Compatibility helpers while old experiments are being migrated.
    def getDemand(self, asset_id: int) -> tuple[str, int]:
        item = self.get_item(asset_id)
        return item.demand_name, item.demand_score

    def getValue(self, asset_id: int) -> int | None:
        return self.get_item(asset_id).roli_value

    def getRAP(self, asset_id: int) -> int:
        return self.get_item(asset_id).rap

    def isProjected(self, asset_id: int) -> bool:
        return self.get_item(asset_id).projected

    def isRare(self, asset_id: int) -> bool:
        return self.get_item(asset_id).rare

    def toName(self, asset_id: int) -> str:
        return self.get_item(asset_id).name

    def postTradeAd(
        self,
        cookies: str,
        items_giving: list[int],
        items_receiving: list[int],
        request_tags: str | list[str],
        player_id: int,
    ) -> dict:
        return self.post_trade_ad(
            cookies,
            items_giving,
            items_receiving,
            request_tags,
            player_id,
        )


# Old class name remains usable.
Rolimons = RolimonsClient
