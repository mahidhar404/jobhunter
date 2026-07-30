"""
Compute real $ cost for a Skyvern task run against DeepSeek, because Skyvern's own
`step_cost` field always reports 0.0 for a custom OPENAI_COMPATIBLE model (litellm has
no pricing table entry for it) -- this is NOT DeepSeek being free, it's the cost
tracking silently no-op'ing. Confirmed by inspecting Skyvern 1.0.47 source directly.

Skyvern's own `input_token_count` / `output_token_count` / `cached_token_count` per
step ARE real numbers (sourced from the LLM response's own `usage` object, not an
estimate -- confirmed in skyvern/forge/sdk/api/llm/api_handler_factory.py). This module
just multiplies those real counts by DeepSeek's real published per-token price.

PRICING NOTE (verify before trusting for real budgeting):
DeepSeek retired the "deepseek-chat" / "deepseek-reasoner" model *names* on
2026-07-24 -- just days before this file was written. Our .env still requests
OPENAI_COMPATIBLE_MODEL_NAME=deepseek-chat, and DeepSeek's API is currently accepting
that name and internally routing it to "deepseek-v4-flash" (confirmed via the
`last_llm_model` field Skyvern records per step during real test runs). That's a
backward-compat alias that could be withdrawn without notice. Pricing below is
DeepSeek V4-Flash's official published rate as of 2026-07-28
(https://api-docs.deepseek.com/quick_start/pricing/):

    input,  cache hit:  $0.0028 / 1M tokens
    input,  cache miss: $0.14   / 1M tokens
    output:              $0.28   / 1M tokens

If DeepSeek changes pricing, or the deepseek-chat -> deepseek-v4-flash alias stops
working and a different model actually serves the request, update PRICE_PER_MILLION
below (and double check `last_llm_model` in real step data to confirm which model is
actually being billed).
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field

PRICE_PER_MILLION = {
    "deepseek-v4-flash": {
        "input_cache_hit": 0.0028,
        "input_cache_miss": 0.14,
        "output": 0.28,
    },
    "deepseek-v4-pro": {
        "input_cache_hit": 0.003625,
        "input_cache_miss": 0.435,
        "output": 0.87,
    },
}

DEFAULT_MODEL = "deepseek-v4-flash"


@dataclass
class StepCost:
    step_id: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float


@dataclass
class TaskCost:
    steps: list[StepCost] = field(default_factory=list)

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.steps)

    @property
    def total_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.steps)

    @property
    def total_cached_tokens(self) -> int:
        return sum(s.cached_tokens for s in self.steps)

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.steps)

    def summary(self) -> dict:
        return {
            "num_steps": len(self.steps),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cached_tokens": self.total_cached_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
        }


def compute_step_cost(step: dict, model: str = DEFAULT_MODEL) -> StepCost:
    """
    step: one element from GET /api/v1/tasks/{task_id}/steps -- expects the real
    Skyvern field names: input_token_count, output_token_count, cached_token_count.
    """
    prices = PRICE_PER_MILLION[model]
    input_tokens = step.get("input_token_count") or 0
    output_tokens = step.get("output_token_count") or 0
    cached_tokens = min(step.get("cached_token_count") or 0, input_tokens)
    uncached_input_tokens = input_tokens - cached_tokens

    cost = (
        uncached_input_tokens / 1_000_000 * prices["input_cache_miss"]
        + cached_tokens / 1_000_000 * prices["input_cache_hit"]
        + output_tokens / 1_000_000 * prices["output"]
    )

    return StepCost(
        step_id=step.get("step_id", "?"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        cost_usd=cost,
    )


def compute_task_cost(steps: list[dict], model: str = DEFAULT_MODEL) -> TaskCost:
    """Takes the raw list returned by GET /api/v1/tasks/{task_id}/steps and returns
    a TaskCost with a per-step breakdown and running totals (tokens + real $ cost)."""
    return TaskCost(steps=[compute_step_cost(s, model=model) for s in steps])


def fetch_and_compute(task_id: str, base_url: str | None = None, api_key: str | None = None,
                       model: str = DEFAULT_MODEL) -> TaskCost:
    """Convenience wrapper: hits the running Skyvern server's steps endpoint directly."""
    base_url = base_url or os.environ.get("SKYVERN_BASE_URL", "http://localhost:8000")
    api_key = api_key or os.environ["SKYVERN_API_KEY"]
    req = urllib.request.Request(
        f"{base_url}/api/v1/tasks/{task_id}/steps",
        headers={"x-api-key": api_key},
    )
    with urllib.request.urlopen(req) as resp:
        steps = json.loads(resp.read())
    return compute_task_cost(steps, model=model)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: python cost_helper.py <task_id>")
        sys.exit(1)

    result = fetch_and_compute(sys.argv[1])
    print(json.dumps(result.summary(), indent=2))
    for s in result.steps:
        print(f"  {s.step_id}: in={s.input_tokens} (cached={s.cached_tokens}) "
              f"out={s.output_tokens} cost=${s.cost_usd:.6f}")
