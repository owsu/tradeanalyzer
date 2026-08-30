from models import ItemSnapshot
from trading.evaluator import TradeEvaluator


class FakeMarket:
    def __init__(self, items):
        self.items = items

    def get_item(self, asset_id: int) -> ItemSnapshot:
        return self.items[asset_id]


def item(
    asset_id: int,
    value: int,
    *,
    demand: int = 2,
    projected: bool = False,
    rare: bool = False,
) -> ItemSnapshot:
    return ItemSnapshot(
        asset_id=asset_id,
        name=f"Item {asset_id}",
        acronym="",
        rap=value,
        roli_value=value,
        default_value=value,
        demand_name="normal",
        demand_score=demand,
        projected=projected,
        rare=rare,
    )


def test_evaluator_returns_structured_result():
    market = FakeMarket({1: item(1, 10_000), 2: item(2, 12_000, demand=3)})
    result = TradeEvaluator(market, estimated_values={}).evaluate([1], [2])

    assert result.effective_value_difference == 2_000
    assert result.score > 50
    assert result.recommendation in {"accept", "review", "decline"}
    assert result.score_components


def test_evaluator_uses_learned_value_before_rolimons_fallback():
    class LearnedValues:
        def learned_item_value(self, asset_id, **kwargs):
            return {
                "value": 15_000,
                "proof_count": 4,
                "source": "direct implied trades",
            } if asset_id == 1 else None

    market = FakeMarket({1: item(1, 10_000), 2: item(2, 12_000)})
    result = TradeEvaluator(
        market, estimated_values={}, learned_values=LearnedValues()
    ).evaluate([1], [2])

    assert result.giving.items[0].effective_value == 15_000
    assert result.giving.items[0].effective_value_source == (
        "implied market (direct implied trades, 4 proofs)"
    )


def test_projected_receive_cannot_auto_accept():
    market = FakeMarket(
        {1: item(1, 10_000), 2: item(2, 30_000, demand=4, projected=True)}
    )
    result = TradeEvaluator(market, estimated_values={}).evaluate([1], [2])

    assert result.recommendation != "accept"
    assert result.receiving.projected_count == 1


def test_known_amazing_demand_beats_unrated_rap_items():
    giving_one = item(1, 6_555)
    giving_two = item(2, 3_343)
    receiving = item(3, 10_000, demand=4)
    giving_one = ItemSnapshot(
        **{**giving_one.__dict__, "demand_name": "none", "demand_score": -1}
    )
    giving_two = ItemSnapshot(
        **{**giving_two.__dict__, "demand_name": "none", "demand_score": -1}
    )
    market = FakeMarket({1: giving_one, 2: giving_two, 3: receiving})

    result = TradeEvaluator(market, estimated_values={}).evaluate([1, 2], [3])

    assert result.trade_type == "upgrade"
    assert result.effective_value_difference == 102
    assert result.demand_difference == 2
    assert result.score_components["demand"] == 8
    assert result.recommendation == "accept"
    assert result.giving.demand_coverage == 0
    assert result.receiving.demand_coverage == 1
    assert result.score_components["trade_direction"] == 15


def test_small_upgrade_overpay_is_still_incentivized():
    market = FakeMarket({1: item(1, 10_000), 2: item(2, 9_800)})
    result = TradeEvaluator(market, estimated_values={}).evaluate([1], [2])

    # Force upgrade classification through a larger receiving item while total
    # receiving value remains a small overpay cost.
    market = FakeMarket(
        {
            1: item(1, 6_000),
            2: item(2, 4_000),
            3: item(3, 9_800),
        }
    )
    result = TradeEvaluator(market, estimated_values={}).evaluate([1, 2], [3])
    assert result.trade_type == "upgrade"
    assert result.score_components["trade_direction"] == 12


def test_extreme_upgrade_overpay_loses_direction_bonus():
    market = FakeMarket(
        {
            1: item(1, 8_000),
            2: item(2, 7_000),
            3: item(3, 10_000),
        }
    )
    result = TradeEvaluator(market, estimated_values={}).evaluate([1, 2], [3])
    assert result.trade_type == "upgrade"
    assert result.score_components["trade_direction"] < 0


def test_terrible_demand_upgrade_cannot_auto_accept():
    market = FakeMarket(
        {
            1: item(1, 6_000),
            2: item(2, 4_000),
            3: item(3, 11_000, demand=0),
        }
    )
    result = TradeEvaluator(market, estimated_values={}).evaluate([1, 2], [3])
    assert result.trade_type == "upgrade"
    assert result.recommendation != "accept"
    assert any("terrible demand" in reason for reason in result.reasons)


def test_weighted_demand_is_dominated_by_the_high_value_item():
    market = FakeMarket(
        {
            1: item(1, 10_000, demand=2),
            2: item(2, 80_000, demand=4),
            3: item(3, 5_000, demand=0),
        }
    )
    result = TradeEvaluator(market, estimated_values={}).evaluate([1], [2, 3])

    assert result.receiving.weighted_demand == 320_000 / 85_000
    assert result.receiving.weighted_demand > 3.75
    assert result.demand_difference > 1.75


def test_projected_penalty_is_weighted_by_value_share():
    market = FakeMarket(
        {
            1: item(1, 40_000),
            4: item(4, 40_000),
            2: item(2, 80_000),
            3: item(3, 5_000, projected=True),
        }
    )
    result = TradeEvaluator(market, estimated_values={}).evaluate([1, 4], [2, 3])

    expected_share = 5_000 / 85_000
    assert result.receiving.projected_value_share == expected_share
    assert result.score_components["projected_risk"] == round(
        -25 * expected_share, 3
    )
    assert result.recommendation == "accept"


def test_near_size_downgrade_is_strongly_rewarded():
    market = FakeMarket(
        {
            1: item(1, 115_000),
            2: item(2, 110_000),
            3: item(3, 10_000),
        }
    )
    result = TradeEvaluator(market, estimated_values={}).evaluate([1], [2, 3])

    assert result.trade_type == "downgrade"
    assert result.receiving.biggest_item_value / result.giving.biggest_item_value > 0.95
    assert result.score_components["trade_direction"] == 15
    assert result.recommendation == "accept"


def test_heavily_fragmented_downgrade_is_penalized():
    market = FakeMarket(
        {
            1: item(1, 100_000),
            2: item(2, 30_000),
            3: item(3, 30_000),
            4: item(4, 30_000),
            5: item(5, 20_000),
        }
    )
    result = TradeEvaluator(market, estimated_values={}).evaluate(
        [1], [2, 3, 4, 5]
    )

    assert result.trade_type == "downgrade"
    assert result.score_components["trade_direction"] == -8
    assert any("heavily fragmented" in reason for reason in result.reasons)
