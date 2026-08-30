from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import requests


class RobloxTradeError(RuntimeError):
    """Raised when Roblox rejects a trade API request."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RobloxAutomationGuard(Protocol):
    def require_roblox_trades_enabled(self) -> None: ...

    def record_trade_action(
        self,
        action: str,
        *,
        target_trade_id: int | None,
        success: bool,
        detail: str | None = None,
    ) -> int: ...


class RobloxTradeClient:
    BASE_URL = "https://trades.roblox.com"
    STATUS_TYPES = {"inbound", "outbound", "completed", "inactive"}
    PAGE_LIMITS = {10, 25, 50, 100}

    def __init__(
        self,
        security_cookie: str,
        user_id: int,
        *,
        automation: RobloxAutomationGuard,
        session: requests.Session | None = None,
        timeout: float = 15.0,
    ) -> None:
        if not security_cookie or not security_cookie.strip():
            raise ValueError("ROBLOX_SECURITY_COOKIE is required")
        if int(user_id) <= 0:
            raise ValueError("ROBLOX_USER_ID must be a positive integer")
        self.user_id = int(user_id)
        self.automation = automation
        self.session = session or requests.Session()
        self.timeout = timeout
        self._csrf_token: str | None = None
        self.session.cookies.set(".ROBLOSECURITY", security_cookie.strip())
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "RobloxTraderPrototype/1.0",
            }
        )

    @staticmethod
    def _positive_id(value: int, label: str) -> int:
        parsed = int(value)
        if parsed <= 0:
            raise ValueError(f"{label} must be a positive integer")
        return parsed

    @classmethod
    def _asset_ids(cls, values: Sequence[int], label: str) -> list[int]:
        parsed = [cls._positive_id(value, label) for value in values]
        if not parsed:
            raise ValueError(f"{label} must contain at least one user asset ID")
        if len(parsed) > 4:
            raise ValueError(f"{label} can contain at most 4 user asset IDs")
        if len(set(parsed)) != len(parsed):
            raise ValueError(f"{label} cannot contain duplicate user asset IDs")
        return parsed

    @staticmethod
    def _robux(value: int, label: str) -> int:
        parsed = int(value)
        if parsed < 0:
            raise ValueError(f"{label} cannot be negative")
        return parsed

    @staticmethod
    def _response_json(response: requests.Response) -> dict:
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise RobloxTradeError(
                "Roblox returned a non-JSON response",
                status_code=response.status_code,
            ) from exc
        return payload if isinstance(payload, dict) else {"data": payload}

    @staticmethod
    def _error_message(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"Roblox request failed with HTTP {response.status_code}"
        errors = payload.get("errors", []) if isinstance(payload, dict) else []
        messages = [str(error.get("message")) for error in errors if error.get("message")]
        return "; ".join(messages) or f"Roblox request failed with HTTP {response.status_code}"

    def _get(self, path: str, *, params: dict | None = None) -> dict:
        try:
            response = self.session.get(
                f"{self.BASE_URL}{path}", params=params, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise RobloxTradeError(f"Roblox request failed: {exc}") from exc
        if not response.ok:
            raise RobloxTradeError(
                self._error_message(response), status_code=response.status_code
            )
        return self._response_json(response)

    def _post(
        self,
        path: str,
        *,
        action: str,
        trade_id: int | None = None,
        payload: dict | None = None,
    ) -> dict:
        url = f"{self.BASE_URL}{path}"
        headers = {"X-CSRF-TOKEN": self._csrf_token} if self._csrf_token else {}
        try:
            self.automation.require_roblox_trades_enabled()
            response = self.session.post(
                url, json=payload, headers=headers, timeout=self.timeout
            )
            csrf_token = response.headers.get("x-csrf-token")
            if response.status_code == 403 and csrf_token:
                self._csrf_token = csrf_token
                self.automation.require_roblox_trades_enabled()
                response = self.session.post(
                    url,
                    json=payload,
                    headers={"X-CSRF-TOKEN": csrf_token},
                    timeout=self.timeout,
                )
            if not response.ok:
                raise RobloxTradeError(
                    self._error_message(response), status_code=response.status_code
                )
            result = self._response_json(response)
        except Exception as exc:
            self.automation.record_trade_action(
                action,
                target_trade_id=trade_id,
                success=False,
                detail=str(exc),
            )
            raise
        self.automation.record_trade_action(
            action,
            target_trade_id=trade_id or result.get("id"),
            success=True,
        )
        return result

    def list_trades(
        self,
        status: str,
        *,
        limit: int = 25,
        cursor: str | None = None,
        sort_order: str = "Desc",
    ) -> dict:
        normalized_status = status.strip().lower()
        if normalized_status not in self.STATUS_TYPES:
            raise ValueError(
                f"status must be one of: {', '.join(sorted(self.STATUS_TYPES))}"
            )
        if int(limit) not in self.PAGE_LIMITS:
            raise ValueError("limit must be one of: 10, 25, 50, 100")
        normalized_order = sort_order.title()
        if normalized_order not in {"Asc", "Desc"}:
            raise ValueError("sort_order must be Asc or Desc")
        params = {"limit": int(limit), "sortOrder": normalized_order}
        if cursor:
            params["cursor"] = cursor
        return self._get(f"/v1/trades/{normalized_status}", params=params)

    def get_trade(self, trade_id: int) -> dict:
        return self._get(f"/v2/trades/{self._positive_id(trade_id, 'trade_id')}")

    def can_trade_with(self, user_id: int) -> dict:
        return self._get(
            f"/v2/users/{self._positive_id(user_id, 'user_id')}/can-trade-with"
        )

    def accept(self, trade_id: int) -> dict:
        trade_id = self._positive_id(trade_id, "trade_id")
        return self._post(f"/v1/trades/{trade_id}/accept", action="accept", trade_id=trade_id)

    def decline(self, trade_id: int) -> dict:
        trade_id = self._positive_id(trade_id, "trade_id")
        return self._post(f"/v1/trades/{trade_id}/decline", action="decline", trade_id=trade_id)

    def _trade_payload(
        self,
        partner_user_id: int,
        giving_user_asset_ids: Sequence[int],
        receiving_user_asset_ids: Sequence[int],
        *,
        giving_robux: int = 0,
        receiving_robux: int = 0,
    ) -> dict:
        partner_user_id = self._positive_id(partner_user_id, "partner_user_id")
        if partner_user_id == self.user_id:
            raise ValueError("Cannot trade with yourself")
        return {
            "offers": [
                {
                    "userId": self.user_id,
                    "userAssetIds": self._asset_ids(
                        giving_user_asset_ids, "giving_user_asset_ids"
                    ),
                    "robux": self._robux(giving_robux, "giving_robux"),
                },
                {
                    "userId": partner_user_id,
                    "userAssetIds": self._asset_ids(
                        receiving_user_asset_ids, "receiving_user_asset_ids"
                    ),
                    "robux": self._robux(receiving_robux, "receiving_robux"),
                },
            ]
        }

    def send(
        self,
        partner_user_id: int,
        giving_user_asset_ids: Sequence[int],
        receiving_user_asset_ids: Sequence[int],
        *,
        giving_robux: int = 0,
        receiving_robux: int = 0,
    ) -> dict:
        payload = self._trade_payload(
            partner_user_id,
            giving_user_asset_ids,
            receiving_user_asset_ids,
            giving_robux=giving_robux,
            receiving_robux=receiving_robux,
        )
        return self._post("/v2/trades/send", action="send", payload=payload)

    def counter(
        self,
        trade_id: int,
        partner_user_id: int,
        giving_user_asset_ids: Sequence[int],
        receiving_user_asset_ids: Sequence[int],
        *,
        giving_robux: int = 0,
        receiving_robux: int = 0,
    ) -> dict:
        trade_id = self._positive_id(trade_id, "trade_id")
        payload = self._trade_payload(
            partner_user_id,
            giving_user_asset_ids,
            receiving_user_asset_ids,
            giving_robux=giving_robux,
            receiving_robux=receiving_robux,
        )
        return self._post(
            f"/v2/trades/{trade_id}/counter",
            action="counter",
            trade_id=trade_id,
            payload=payload,
        )
