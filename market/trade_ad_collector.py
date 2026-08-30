from __future__ import annotations

from clients.rolimons import RolimonsClient
from database import Database


class TradeAdCollector:
    """Ingest Rolimon's permissioned recent-ad feed into discovery queues."""

    def __init__(
        self, database: Database, client: RolimonsClient, *,
        repromote_seconds: int = 1800,
        asset_priority_seconds: int = 21600,
    ) -> None:
        self.database = database
        self.client = client
        self.repromote_seconds = max(int(repromote_seconds), 1)
        self.asset_priority_seconds = max(int(asset_priority_seconds), 1)

    def collect(self) -> dict:
        ads = self.client.recent_trade_ads()
        return self.database.ingest_rolimons_trade_ads(
            ads,
            repromote_seconds=self.repromote_seconds,
            asset_priority_seconds=self.asset_priority_seconds,
        )
