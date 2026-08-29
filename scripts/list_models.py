from __future__ import annotations

import argparse

from clients.gemini import GeminiClient


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List Gemini models available to your API key"
    )
    parser.add_argument(
        "--contains",
        default="",
        help="Only show model names containing this text (case-insensitive)",
    )
    args = parser.parse_args()

    names = GeminiClient().list_model_names(args.contains)
    for name in names:
        print(name)
    print(f"\n{len(names)} model(s) shown")


if __name__ == "__main__":
    main()
