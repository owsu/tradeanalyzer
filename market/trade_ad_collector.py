from __future__ import annotations

from clients.rolimons import RolimonsClient
from database import Database


class TradeAdCollector:
    """Ingest Rolimon's permissioned recent-ad feed into discovery queues."""

    def __init__(
        self, database: Database, client: RolimonsClient, *,
        repromote_seconds: int = 1800,
        asset_priority_seconds: int = 21600,
        admission_window_seconds: int = 86400,
        admission_min_ads: int = 3,
        admission_min_offer_value: int = 100000,
        admission_max_users: int = 500,
        asset_min_offers: int = 3,
        asset_max_priority: int = 200,
    ) -> None:
        self.database = database
        self.client = client
        self.repromote_seconds = max(int(repromote_seconds), 1)
        self.asset_priority_seconds = max(int(asset_priority_seconds), 1)
        self.admission_window_seconds = max(int(admission_window_seconds), 1)
        self.admission_min_ads = max(int(admission_min_ads), 1)
        self.admission_min_offer_value = max(int(admission_min_offer_value), 0)
        self.admission_max_users = max(int(admission_max_users), 1)
        self.asset_min_offers = max(int(asset_min_offers), 1)
        self.asset_max_priority = max(int(asset_max_priority), 1)

    def collect(self) -> dict:
        ads = self.client.recent_trade_ads()
        return self.database.ingest_rolimons_trade_ads(
            ads,
            repromote_seconds=self.repromote_seconds,
            asset_priority_seconds=self.asset_priority_seconds,
            admission_window_seconds=self.admission_window_seconds,
            admission_min_ads=self.admission_min_ads,
            admission_min_offer_value=self.admission_min_offer_value,
            admission_max_users=self.admission_max_users,
            asset_min_offers=self.asset_min_offers,
            asset_max_priority=self.asset_max_priority,
        )
