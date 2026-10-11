"""Check that the configured providers are usable, without spending money by default.

python -m pipeline.check_provider                 # selected providers, free checks only
python -m pipeline.check_provider --llm openai    # check OpenAI regardless of config
python -m pipeline.check_provider --llm openai --live-call   # also one tiny BILLABLE request
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core.config import load_settings
from core.costs import estimate_cost_usd
from core.logging import configure_logging
from pipeline.stage import EXIT_OK, handle_errors
from providers.registry import build_providers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.check_provider", description=__doc__)
    parser.add_argument("--llm", help="LLM provider to check (default: the configured one)")
    parser.add_argument(
        "--live-call",
        action="store_true",
        help="also make one tiny real request (costs a fraction of a cent)",
    )
    parser.add_argument("--config-dir", type=Path)
    args = parser.parse_args(argv)

    def action() -> int:
        settings = load_settings(args.config_dir)
        if args.llm:
            settings.providers.selected.llm = args.llm
        configure_logging(settings.log_level)
        llm = build_providers(settings).llm

        print(f"LLM provider: {llm.name}   model: {llm.model}")
        print(llm.healthcheck())
        if not args.live_call:
            print("Free check passed. Add --live-call to make one small billable request.")
            return EXIT_OK

        response = llm.generate("Reply with the single word: ok", temperature=0, max_tokens=16)
        price = settings.providers.pricing.get(llm.name, {}).get(llm.model)
        cost = estimate_cost_usd(price, response.usage)
        print(f"Live call reply: {response.text.strip()!r}")
        print(
            f"Tokens in/out: {response.usage.tokens_in}/{response.usage.tokens_out}; "
            f"estimated cost: ${cost:.6f}" + ("" if price else " (no pricing configured)")
        )
        return EXIT_OK

    return handle_errors(action)


if __name__ == "__main__":
    raise SystemExit(main())
