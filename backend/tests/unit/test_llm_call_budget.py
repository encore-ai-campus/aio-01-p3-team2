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


def test_live_usage_is_measured_and_budget_overrun_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_ENGINE", "mock")
    monkeypatch.setenv("CANONICAL_PLANNING_MODE", "live")
    monkeypatch.setenv("LLM_MAX_INPUT_TOKENS", "10")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "10")
    monkeypatch.setenv("LLM_MAX_COST_USD", "1")
    monkeypatch.setenv("LLM_INPUT_USD_PER_MILLION", "2")
    monkeypatch.setenv("LLM_OUTPUT_USD_PER_MILLION", "4")
    monkeypatch.setenv("LLM_PRICING_VERSION", "test-price-v1")
    monkeypatch.setenv("LLM_COST_CURRENCY", "USD")
    monkeypatch.setattr("backend.app.services.llm_service.validate_live_recording", lambda: None)
    monkeypatch.setattr(
        "backend.app.services.llm_service.write_live_record",
        lambda **record: {"record_id": "record-1", "prompt_hash": "hash-1"},
    )

    async def transport(agent: str, context: dict[str, object]) -> dict[str, object]:
        return {
            "ok": True,
            "__llm_usage__": {"input_tokens": 11, "output_tokens": 2},
            "__llm_record__": {
                "model_id": "test-model",
                "prompt_template_version": "v1",
                "prompt": "prompt",
                "response": "response",
            },
        }

    service = LLMService(transport, 1, source="live")

    async def run() -> LLMBudgetExceeded:
        with (
            workflow_llm_call_budget(max_calls=2),
            pytest.raises(LLMBudgetExceeded, match="input token") as exc_info,
        ):
            await service.generate("planner", {})
        return exc_info.value

    error = asyncio.run(run())
    assert error.usage is not None
    assert error.usage["input_tokens"] == 11


def test_live_workflow_requires_price_and_usage_configuration(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_ENGINE", "backend_b_live")
    monkeypatch.setenv("LLM_PRICING_VERSION", "test-price-v1")
    monkeypatch.setenv("LLM_COST_CURRENCY", "USD")
    monkeypatch.setenv("LLM_MAX_INPUT_TOKENS", "100")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "100")
    monkeypatch.setenv("LLM_MAX_COST_USD", "1")
    monkeypatch.delenv("LLM_INPUT_USD_PER_MILLION", raising=False)
    with (
        pytest.raises(LLMBudgetExceeded, match="LLM_INPUT_USD_PER_MILLION"),
        workflow_llm_call_budget(max_calls=8),
    ):
        pass


def test_live_usage_cost_overrun_is_exact_and_includes_measured_usage(monkeypatch) -> None:
    monkeypatch.setenv("CANONICAL_PLANNING_MODE", "live")
    monkeypatch.setenv("LLM_COST_CURRENCY", "USD")
    monkeypatch.setenv("LLM_PRICING_VERSION", "fixture-price-v1")
    monkeypatch.setenv("LLM_MAX_INPUT_TOKENS", "100")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "100")
    monkeypatch.setenv("LLM_MAX_COST_USD", "0.000001")
    monkeypatch.setenv("LLM_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("LLM_OUTPUT_USD_PER_MILLION", "1")
    monkeypatch.setattr("backend.app.services.llm_service.validate_live_recording", lambda: None)
    monkeypatch.setattr(
        "backend.app.services.llm_service.write_live_record",
        lambda **record: {"record_id": "record-2", "prompt_hash": "hash-2"},
    )

    async def transport(agent: str, context: dict[str, object]) -> dict[str, object]:
        return {
            "ok": True,
            "__llm_usage__": {"input_tokens": 1, "output_tokens": 1},
            "__llm_record__": {
                "model_id": "test-model",
                "prompt_template_version": "v1",
                "prompt": "prompt",
                "response": "response",
            },
        }

    service = LLMService(transport, 1, source="live")

    async def run() -> LLMBudgetExceeded:
        with (
            workflow_llm_call_budget(max_calls=2),
            pytest.raises(LLMBudgetExceeded, match="비용 예산") as exc_info,
        ):
            await service.generate("planner", {})
        return exc_info.value

    error = asyncio.run(run())
    assert error.usage is not None
    assert error.usage["cost"] == "0.000002"
