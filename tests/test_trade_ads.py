from datetime import UTC, datetime, timedelta

from clients.rolimons import RecentTradeAd, RolimonsClient
from database import Database


class Response:
    status_code = 200
    headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "success": True,
            "trade_ads": [[
                10, 1_700_000_000, 20, "Trader",
                {"items": [1001, 1002], "robux": 50},
                {"items": [2001], "tags": [1, 5]},
            ]],
        }


class Session:
    def get(self, url, timeout):
        assert url.endswith("/tradeads/v1/getrecentads")
        return Response()


def test_recent_trade_ads_are_parsed():
    ads = RolimonsClient(session=Session(), auto_refresh=False).recent_trade_ads()
    assert ads == [RecentTradeAd(
        ad_id=10, created_at=1_700_000_000, user_id=20, username="Trader",
        offer_items=(1001, 1002), request_items=(2001,), offer_robux=50,
        request_robux=0, request_tags=(1, 5),
    )]


def test_trade_ads_are_deduplicated_and_promote_discovery(tmp_path):
    database = Database(tmp_path / "market.db")
    for asset_id in (1001, 1002, 2001):
        database.upsert_tracked_asset(
            asset_id, name="Item", market_value=10_000, interval_seconds=3600
        )
    ad = RecentTradeAd(
        ad_id=10, created_at=int(datetime.now(UTC).timestamp()), user_id=20,
        username="Trader", offer_items=(1001, 1002), request_items=(2001,),
        offer_robux=0, request_robux=0, request_tags=(5,),
    )

    first = database.ingest_rolimons_trade_ads(
        [ad], admission_min_ads=1, asset_min_offers=1
    )
    second = database.ingest_rolimons_trade_ads(
        [ad], admission_min_ads=1, asset_min_offers=1
    )

    assert first["new_ads"] == 1
    assert second["new_ads"] == 0
    assert database.due_watched_user_ids(10) == [20]
    with database.connect() as connection:
        scores = {
            row["asset_id"]: row["priority_score"]
            for row in connection.execute(
                "SELECT asset_id, priority_score FROM tracked_assets"
            )
        }
    assert scores[1001] >= 80
    assert scores[1002] >= 80
    assert scores[2001] == 0
    status = database.market_status()
    assert status["rolimons_trade_ads"] == 1
    assert status["rolimons_trade_advertisers"] == 1
    assert status["rolimons_advertised_assets"] == 3


def test_trade_ad_users_cool_through_tiers_then_reactivate(tmp_path):
    database = Database(tmp_path / "market.db")
    database.add_watched_user(20, source="trade_ad")

    def delay_for(age):
        with database.connect() as connection:
            connection.execute(
                "UPDATE watched_users SET last_trade_ad_at=? WHERE user_id=20",
                ((datetime.now(UTC) - age).isoformat(),),
            )
            connection.commit()
        return database.recommended_watched_user_delay(
            20, default_seconds=1800,
            hot_window_seconds=21600, hot_interval_seconds=1800,
            warm_window_seconds=259200, warm_interval_seconds=21600,
            active_window_seconds=2592000, active_interval_seconds=86400,
            cold_interval_seconds=604800,
        )

    assert delay_for(timedelta(hours=1)) == 1800
    assert delay_for(timedelta(days=1)) == 21600
    assert delay_for(timedelta(days=10)) == 86400
    assert delay_for(timedelta(days=100)) == 604800

    delay_for(timedelta(days=366))
    assert database.archive_inactive_trade_ad_users(365) == 1
    assert database.watched_user_ids() == []
    assert database.market_status()["archived_trade_ad_users"] == 1

    new_ad = RecentTradeAd(
        ad_id=11, created_at=int(datetime.now(UTC).timestamp()), user_id=20,
        username="Trader", offer_items=(), request_items=(), offer_robux=0,
        request_robux=0, request_tags=(),
    )
    database.ingest_rolimons_trade_ads([new_ad], admission_min_ads=1)
    assert database.watched_user_ids() == [20]
    assert database.market_status()["archived_trade_ad_users"] == 0


def test_trade_ad_admission_is_capped(tmp_path):
    database = Database(tmp_path / "market.db")
    now = int(datetime.now(UTC).timestamp())
    ads = [RecentTradeAd(
        ad_id=100 + user_id, created_at=now, user_id=user_id,
        username=f"Trader{user_id}", offer_items=(), request_items=(),
        offer_robux=0, request_robux=0, request_tags=(),
    ) for user_id in range(1, 6)]

    result = database.ingest_rolimons_trade_ads(
        ads, admission_min_ads=1, admission_max_users=2
    )

    assert result["admitted_users"] == 2
    assert len(database.watched_user_ids()) == 2
