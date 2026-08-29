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


def test_projected_receive_cannot_auto_accept():
    market = FakeMarket(
        {1: item(1, 10_000), 2: item(2, 30_000, demand=4, projected=True)}
    )
    result = TradeEvaluator(market, estimated_values={}).evaluate([1], [2])

    assert result.recommendation != "accept"
    assert result.receiving.projected_count == 1
