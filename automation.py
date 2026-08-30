from __future__ import annotations

from database import Database


class AutomationPausedError(RuntimeError):
    """Raised when an external mutation is blocked by a pause switch."""


class AutomationController:
    def __init__(self, database: Database) -> None:
        self.database = database

    def state(self) -> dict:
        return self.database.get_automation_state()

    def update(
        self,
        changes: dict[str, bool],
        *,
        actor: str,
        reason: str | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        return self.database.update_automation_state(
            changes,
            actor=actor,
            reason=reason,
            expected_revision=expected_revision,
        )

    def require_trade_ads_enabled(self) -> None:
        state = self.state()
        if not state["trade_ads_enabled"]:
            raise AutomationPausedError("Rolimons trade-ad automation is paused")

    def require_roblox_trades_enabled(self) -> None:
        state = self.state()
        if not state["roblox_trades_enabled"]:
            raise AutomationPausedError("Roblox trade automation is paused")

    def record_trade_action(
        self,
        action: str,
        *,
        target_trade_id: int | None,
        success: bool,
        detail: str | None = None,
    ) -> int:
        return self.database.record_trade_action(
            action,
            target_trade_id=target_trade_id,
            success=success,
            detail=detail,
        )
