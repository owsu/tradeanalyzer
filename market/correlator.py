from __future__ import annotations

from database import Database


class TradeCorrelator:
    """Infer only reciprocal UAID swaps; one-way sales remain unmatched."""

    def __init__(self, database: Database, *, window_seconds: int = 600) -> None:
        self.database = database
        self.window_seconds = int(window_seconds)

    def correlate(self) -> list[int]:
        return self.database.correlate_reciprocal_transfers(self.window_seconds)
