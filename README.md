# Roblox Trader Prototype

The project is split by responsibility so each part can grow independently without turning one file into a giant bot script.

## Layout

```text
.
├── main.py                    # Flask/API entry point
├── config.py                  # env + tunable scoring constants
├── models.py                  # shared data structures
├── clients/
│   ├── rolimons.py            # Rolimons HTTP/data access
│   └── gemini.py              # Gemini provider wrapper
├── trading/
│   ├── evaluator.py           # coordinates trade evaluation
│   ├── scoring.py             # scoring math
│   └── risk.py                # recommendation gates + reasons
├── proofs/
│   ├── parser.py              # LLM extraction prompt + schema conversion
│   ├── validator.py           # deterministic arithmetic verification
│   └── ingestion.py           # source-agnostic RawProof entry point
├── integrations/
│   └── discord_bot.py         # Discord-specific proof collection
├── scripts/
│   ├── evaluate_trade.py      # interactive trade evaluator
│   └── list_models.py         # inspect Gemini models
└── tests/
    ├── test_evaluator.py
    └── test_proofs.py
```

## Setup

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in the credentials you actually use.

## Run

Run these commands from the project root.

Interactive trade evaluator:

```bash
python -m scripts.evaluate_trade
```

Flask API:

```bash
python main.py
```

Discord bot:

```bash
python -m integrations.discord_bot
```

List Gemini models:

```bash
python -m scripts.list_models
```

Tests:

```bash
python -m pytest -q
```

## Import examples

New code should import the implementation directly from its owning module:

```python
from clients.rolimons import RolimonsClient
from trading.evaluator import TradeEvaluator
from proofs.parser import parse_proof
from proofs.validator import validate_proof
```

There are intentionally no compatibility wrapper modules at the project root.
