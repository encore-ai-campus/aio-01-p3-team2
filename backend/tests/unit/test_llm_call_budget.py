"""Per-workflow LLM call budgets stop before the provider is invoked."""

import asyncio

import pytest
from backend.app.services.llm_service import (
    LLMBudgetExceeded,
    LLMService,
    workflow_llm_call_budget,
)


def test_workflow_budget_blocks_provider_call_after_limit() -> None:
    calls = 0

    async def transport(agent: str, context: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    service = LLMService(transport, 1)

    async def run() -> None:
        with workflow_llm_call_budget(max_calls=2):
            await service.generate("planner", {})
            await service.generate("critic", {})
            with pytest.raises(LLMBudgetExceeded):
                await service.generate("planner", {})

    asyncio.run(run())
    assert calls == 2


def test_workflow_budget_is_isolated_between_runs() -> None:
    calls = 0

    async def transport(agent: str, context: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    service = LLMService(transport, 1)

    async def run() -> None:
        for _ in range(2):
            with workflow_llm_call_budget(max_calls=1):
                await service.generate("planner", {})

    asyncio.run(run())
    assert calls == 2
