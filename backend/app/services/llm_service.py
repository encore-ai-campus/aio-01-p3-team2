"""Agent의 LLM 호출에 제한 시간과 공통 오류 처리를 적용한다."""

import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

LLMTransport = Callable[[str, dict[str, object]], Awaitable[Mapping[str, object]]]


class LLMTimeoutError(TimeoutError):
    """LLM 응답이 제한 시간 안에 도착하지 않은 상태다."""


class LLMExecutionError(RuntimeError):
    """LLM 호출이나 응답 형식 확인에 실패한 상태다."""


class LLMTransientError(LLMExecutionError):
    """A provider connection, throttling or server failure permits policy review."""

    error_type = "llm_transient_error"


class LLMBudgetExceeded(LLMExecutionError):
    """Workflow가 허용된 LLM 호출 수를 모두 사용한 상태다."""

    def __init__(self, message: str, *, usage: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class LLMOutputContractError(LLMExecutionError):
    """An Agent response failed its role-specific output schema."""

    def __init__(self, message: str, *, error_type: str) -> None:
        super().__init__(message)
        self.error_type = error_type


def _positive_int_env(name: str) -> int:
    value = os.getenv(name, "").strip()
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise LLMBudgetExceeded(f"{name} 설정이 필요합니다.") from exc
    if parsed < 1:
        raise LLMBudgetExceeded(f"{name}은 양의 정수여야 합니다.")
    return parsed


def _positive_decimal_env(name: str) -> Decimal:
    try:
        parsed = Decimal(os.getenv(name, "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise LLMBudgetExceeded(f"{name} 설정이 필요합니다.") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise LLMBudgetExceeded(f"{name}은 양수여야 합니다.")
    return parsed


def validate_live_recording() -> None:
    """Lazily verify the encrypted raw-record store before provider traffic."""
    from backend.app.services.llm_record_service import validate_live_recording as validate

    validate()


def write_live_record(**record: object) -> dict[str, object]:
    """Persist raw invocation material using the isolated encrypted store."""
    from backend.app.services.llm_record_service import store_llm_record

    return store_llm_record(**record)


@dataclass
class _LLMCallBudget:
    max_calls: int
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_cost: Decimal | None = None
    input_price_per_million: Decimal | None = None
    output_price_per_million: Decimal | None = None
    used_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal(0)
    records: list[dict[str, object]] | None = None

    def consume(self) -> None:
        if self.used_calls >= self.max_calls:
            raise LLMBudgetExceeded("Workflow LLM 호출 한도에 도달했습니다.")
        self.used_calls += 1

    def record_usage(
        self, usage: Mapping[str, object], record: Mapping[str, object] | None = None
    ) -> None:
        try:
            input_tokens = int(usage["input_tokens"])
            output_tokens = int(usage["output_tokens"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMBudgetExceeded("Provider token usage를 확인할 수 없습니다.") from exc
        if input_tokens < 0 or output_tokens < 0:
            raise LLMBudgetExceeded("Provider token usage가 올바르지 않습니다.")
        if self.input_price_per_million is None or self.output_price_per_million is None:
            raise LLMBudgetExceeded("LLM 가격표가 없어 비용 예산을 확인할 수 없습니다.")
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        configured_cost = (
            Decimal(input_tokens) * self.input_price_per_million
            + Decimal(output_tokens) * self.output_price_per_million
        ) / Decimal(1_000_000)
        reported_cost = usage.get("reported_cost")
        if reported_cost is not None:
            try:
                invocation_cost = Decimal(str(reported_cost))
            except InvalidOperation as exc:
                raise LLMBudgetExceeded(
                    "Provider reported cost is invalid.", usage=self.snapshot()
                ) from exc
            if not invocation_cost.is_finite() or invocation_cost < 0:
                raise LLMBudgetExceeded("Provider reported cost is invalid.", usage=self.snapshot())
            self.cost += invocation_cost
        else:
            self.cost += configured_cost
        if record is not None:
            if self.records is None:
                self.records = []
            self.records.append(dict(record))
        if self.max_input_tokens is not None and self.input_tokens > self.max_input_tokens:
            raise LLMBudgetExceeded(
                "Workflow input token 예산을 초과했습니다.", usage=self.snapshot()
            )
        if self.max_output_tokens is not None and self.output_tokens > self.max_output_tokens:
            raise LLMBudgetExceeded(
                "Workflow output token 예산을 초과했습니다.", usage=self.snapshot()
            )
        if self.max_cost is not None and self.cost > self.max_cost:
            raise LLMBudgetExceeded("Workflow 비용 예산을 초과했습니다.", usage=self.snapshot())

    def snapshot(self) -> dict[str, object]:
        return {
            "calls": self.used_calls,
            "input_tokens": self.input_tokens if self.max_cost is not None else None,
            "output_tokens": self.output_tokens if self.max_cost is not None else None,
            "cost": str(self.cost) if self.max_cost is not None else None,
            "currency": "USD" if self.max_cost is not None else None,
            "pricing_version": os.getenv("LLM_PRICING_VERSION")
            if self.max_cost is not None
            else None,
            "records": list(self.records or []),
        }


_CALL_BUDGET: ContextVar[_LLMCallBudget | None] = ContextVar(
    "workflow_llm_call_budget", default=None
)


@contextmanager
def workflow_llm_call_budget(max_calls: int = 8):
    """Apply a hard, per-Workflow cap before any provider request is sent."""
    if type(max_calls) is not int or max_calls < 1:
        raise ValueError("LLM 호출 한도는 양의 정수여야 합니다.")
    live = os.getenv(
        "CANONICAL_PLANNING_MODE", "deterministic"
    ).strip().casefold() == "live" or os.getenv("WORKFLOW_ENGINE", "mock").strip().casefold() in {
        "agent_live",
        "backend_b_live",
    }
    settings: dict[str, object] = {}
    if live:
        currency = os.getenv("LLM_COST_CURRENCY", "USD").strip().upper()
        if currency != "USD" or not os.getenv("LLM_PRICING_VERSION", "").strip():
            raise LLMBudgetExceeded("지원되는 통화(USD)와 LLM_PRICING_VERSION 설정이 필요합니다.")
        settings = {
            "max_input_tokens": _positive_int_env("LLM_MAX_INPUT_TOKENS"),
            "max_output_tokens": _positive_int_env("LLM_MAX_OUTPUT_TOKENS"),
            "max_cost": _positive_decimal_env("LLM_MAX_COST_USD"),
            "input_price_per_million": _positive_decimal_env("LLM_INPUT_USD_PER_MILLION"),
            "output_price_per_million": _positive_decimal_env("LLM_OUTPUT_USD_PER_MILLION"),
        }
        validate_live_recording()
    token = _CALL_BUDGET.set(_LLMCallBudget(max_calls=max_calls, **settings))
    try:
        yield
    finally:
        _CALL_BUDGET.reset(token)


def workflow_llm_usage_snapshot() -> dict[str, object] | None:
    """Return the current execution's measured provider budget summary."""
    budget = _CALL_BUDGET.get()
    return budget.snapshot() if budget is not None else None


class LLMService:
    """주입된 전송 함수로 Agent별 LLM 응답을 받는다.

    실제 LLM SDK 호출과 Mock 호출은 같은 전송 함수 규격을 사용한다.
    응답의 필드별 Schema 검증은 각 Agent와 A의 공통 Validation이 담당한다.
    """

    def __init__(
        self, transport: LLMTransport, timeout_seconds: float, *, source: str = "mock"
    ) -> None:
        """LLM 전송 함수와 호출당 제한 시간을 설정한다."""
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise TypeError("LLM 제한 시간은 숫자여야 합니다.")
        if timeout_seconds <= 0:
            raise ValueError("LLM 제한 시간은 0보다 커야 합니다.")
        if source not in {"mock", "live"}:
            raise ValueError("LLM 전송 출처가 올바르지 않습니다.")
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self.source = source

    async def generate(self, agent_name: str, context: Mapping[str, object]) -> dict[str, object]:
        """Agent Context를 전달하고 구조화된 LLM 응답을 반환한다.

        시간 초과는 재시도 가능한 ``LLMTimeoutError``로 구분한다.
        그 밖의 SDK 오류와 응답 형식 오류는 ``LLMExecutionError``로
        전달하며, 외부 예외의 상세 내용은 상위 계층에 노출하지 않는다.
        """
        if not agent_name:
            raise ValueError("Agent 이름이 비어 있습니다.")
        if not isinstance(context, Mapping):
            raise TypeError("Agent Context는 Mapping이어야 합니다.")

        budget = _CALL_BUDGET.get()
        if budget is not None:
            budget.consume()

        try:
            from backend.app.fault_injection.injector import perturb

            boundary = "critic" if agent_name == "canonical_event_critic_agent" else "planner"
            perturb(f"{boundary}.call")
            response = await asyncio.wait_for(
                self._transport(agent_name, dict(context)),
                timeout=self._timeout_seconds,
            )
            response = perturb(f"{boundary}.response", response)
            if agent_name == "event_planner_agent":
                response = perturb("legacy.planner.response", response)
        except TimeoutError as exc:
            raise LLMTimeoutError("LLM 응답 시간이 초과되었습니다.") from exc
        except LLMBudgetExceeded:
            raise
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if (
                isinstance(exc, (LLMTransientError, ConnectionError))
                or (type(status) is int and (status in {408, 409, 429} or 500 <= status <= 599))
                or type(exc).__name__
                in {"APIConnectionError", "RateLimitError", "InternalServerError"}
            ):
                raise LLMTransientError("LLM 일시 장애로 호출을 완료하지 못했습니다.") from exc
            raise LLMExecutionError("LLM 호출에 실패했습니다.") from exc

        if not isinstance(response, Mapping):
            raise LLMExecutionError("LLM 응답 형식이 올바르지 않습니다.")
        result = dict(response)
        usage = result.pop("__llm_usage__", None)
        record_payload = result.pop("__llm_record__", None)
        if self.source == "live":
            if (
                budget is None
                or not isinstance(usage, Mapping)
                or not isinstance(record_payload, Mapping)
            ):
                raise LLMBudgetExceeded("Provider token usage 또는 Workflow 예산이 없습니다.")
            record = write_live_record(
                agent_name=agent_name,
                model_id=str(record_payload["model_id"]),
                prompt_template_version=str(record_payload["prompt_template_version"]),
                prompt=str(record_payload["prompt"]),
                response=str(record_payload["response"]),
                usage=dict(usage),
            )
            budget.record_usage(usage, record)
        return result


def create_openai_transport(model: str, agents_config: Mapping[str, object]) -> LLMTransport:
    """OpenAI Responses API를 사용하는 네 Agent의 전송 함수를 만든다.

    SDK와 API 키는 실제 서비스 실행 환경에서 제공한다. 이 함수는 키를
    인자로 받거나 로그에 남기지 않는다. 출력 Schema 검증은 SDK와 A의
    공통 Output Validation에서 수행한다.
    """
    if not isinstance(model, str) or not model.strip():
        raise ValueError("OpenAI 모델 이름이 필요합니다.")
    workers = agents_config.get("workers")
    if not isinstance(workers, Mapping):
        raise TypeError("Agent 설정에 workers가 필요합니다.")
    from openai import APITimeoutError, AsyncOpenAI

    async def transport(agent_name: str, context: dict[str, object]) -> Mapping[str, object]:
        """Agent별 지시와 JSON Schema로 구조화된 응답을 요청한다."""
        worker = workers.get(agent_name)
        if not isinstance(worker, Mapping) or agent_name not in _OUTPUT_SCHEMAS:
            raise ValueError("LLM 호출을 허용하지 않은 Agent입니다.")
        instructions = worker.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError("Agent 지시문이 없습니다.")

        # Supervisor owns retry counts and budgets; disable hidden SDK retries.
        client = AsyncOpenAI(max_retries=0)
        try:
            response = await client.responses.create(
                model=model,
                max_output_tokens=_positive_int_env("LLM_MAX_OUTPUT_TOKENS"),
                instructions=instructions,
                input=json.dumps(context, ensure_ascii=False),
                text={
                    "format": {
                        "type": "json_schema",
                        "name": agent_name,
                        "strict": True,
                        "schema": _OUTPUT_SCHEMAS[agent_name],
                    }
                },
            )
        except APITimeoutError as exc:
            raise TimeoutError("LLM 요청 시간이 초과되었습니다.") from exc
        finally:
            await client.close()

        if not response.output_text:
            raise ValueError("LLM의 구조화된 응답이 없습니다.")
        parsed = json.loads(response.output_text)
        if not isinstance(parsed, dict):
            raise TypeError("LLM 응답은 객체여야 합니다.")
        usage = getattr(response, "usage", None)
        if usage is None:
            raise LLMBudgetExceeded("Provider token usage가 응답에 없습니다.")
        parsed["__llm_usage__"] = {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "reported_cost": getattr(usage, "cost", None),
        }
        parsed["__llm_record__"] = {
            "model_id": model,
            "prompt_template_version": hashlib.sha256(
                json.dumps(
                    {
                        "instructions": instructions,
                        "output_schema": _OUTPUT_SCHEMAS[agent_name],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:16],
            "prompt": json.dumps(
                {"instructions": instructions, "input": context},
                ensure_ascii=False,
                sort_keys=True,
            ),
            "response": response.output_text,
        }
        return parsed

    return transport


_OUTPUT_SCHEMAS: dict[str, dict[str, object]] = {
    "canonical_event_planner_agent": {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "action_rationales", "reward_rationales", "reward_proposals"],
        "properties": {
            "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
            "action_rationales": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["rationale"],
                    "properties": {
                        "rationale": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                },
            },
            "reward_rationales": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["rationale"],
                    "properties": {
                        "rationale": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                },
            },
            "reward_proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["action_index", "item_id", "quantity", "rationale"],
                    "properties": {
                        "action_index": {"type": "integer", "minimum": 0},
                        "item_id": {"type": "string", "minLength": 1, "maxLength": 120},
                        "quantity": {"type": "integer", "minimum": 1},
                        "rationale": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                },
            },
        },
    },
    "canonical_event_critic_agent": {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "reason", "review_note", "repairability", "findings"],
        "properties": {
            "status": {"type": "string", "enum": ["PASS", "REVIEW"]},
            "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
            "review_note": {"type": "string", "minLength": 1, "maxLength": 1000},
            "repairability": {"type": "string", "enum": ["HUMAN_REVIEW", "PLANNER_EXPLANATION"]},
            "findings": {
                "type": "array",
                "maxItems": 16,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "field_path", "reason"],
                    "properties": {
                        "kind": {"type": "string", "enum": ["SEMANTIC", "SCOPE"]},
                        "field_path": {"type": "string", "minLength": 1, "maxLength": 200},
                        "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
                    },
                },
            },
        },
    },
    "balance_rule_agent": {
        "type": "object",
        "additionalProperties": False,
        "required": ["is_valid", "reason"],
        "properties": {
            "is_valid": {"type": "boolean"},
            "reason": {"type": "string"},
        },
    },
    "event_planner_agent": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "target_user",
            "duration",
            "event_concept",
            "operation_summary",
            "mission_operation",
            "reward_design",
            "missions",
            "rewards",
        ],
        "properties": {
            "target_user": {"type": "string", "enum": ["new_user", "returning_user", "all_user"]},
            "duration": {"type": "integer", "minimum": 1, "maximum": 7},
            "event_concept": {"type": "string"},
            "operation_summary": {"type": "string"},
            "mission_operation": {"type": "string"},
            "reward_design": {"type": "string"},
            "missions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "day",
                        "content_id",
                        "mission_type",
                        "target_count",
                        "mission_name",
                        "day_title",
                        "planning_reason",
                    ],
                    "properties": {
                        "day": {"type": "integer"},
                        "content_id": {"type": "string"},
                        "mission_type": {"type": "string"},
                        "target_count": {"type": "integer"},
                        "mission_name": {"type": "string"},
                        "day_title": {"type": "string"},
                        "planning_reason": {"type": "string"},
                    },
                },
            },
            "rewards": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["day", "item_id", "quantity"],
                    "properties": {
                        "day": {"type": "integer"},
                        "item_id": {"type": "string"},
                        "quantity": {"type": "integer"},
                    },
                },
            },
        },
    },
    "critic_agent": {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "error_type", "reason", "review_note"],
        "properties": {
            "status": {"type": "string", "enum": ["PASS", "RETRY", "FAIL"]},
            "error_type": {"type": ["string", "null"]},
            "reason": {"type": "string"},
            "review_note": {"type": "string"},
        },
    },
}
