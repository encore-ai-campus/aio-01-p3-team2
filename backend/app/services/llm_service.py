"""Agent의 LLM 호출에 제한 시간과 공통 오류 처리를 적용한다."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping

LLMTransport = Callable[[str, dict[str, object]], Awaitable[Mapping[str, object]]]


class LLMTimeoutError(TimeoutError):
    """LLM 응답이 제한 시간 안에 도착하지 않은 상태다."""


class LLMExecutionError(RuntimeError):
    """LLM 호출이나 응답 형식 확인에 실패한 상태다."""


class LLMService:
    """주입된 전송 함수로 Agent별 LLM 응답을 받는다.

    실제 LLM SDK 호출과 Mock 호출은 같은 전송 함수 규격을 사용한다.
    응답의 필드별 Schema 검증은 각 Agent와 A의 공통 Validation이 담당한다.
    """

    def __init__(self, transport: LLMTransport, timeout_seconds: float) -> None:
        """LLM 전송 함수와 호출당 제한 시간을 설정한다."""
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise TypeError("LLM 제한 시간은 숫자여야 합니다.")
        if timeout_seconds <= 0:
            raise ValueError("LLM 제한 시간은 0보다 커야 합니다.")
        self._transport = transport
        self._timeout_seconds = timeout_seconds

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

        try:
            response = await asyncio.wait_for(
                self._transport(agent_name, dict(context)),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            raise LLMTimeoutError("LLM 응답 시간이 초과되었습니다.") from exc
        except Exception as exc:
            raise LLMExecutionError("LLM 호출에 실패했습니다.") from exc

        if not isinstance(response, Mapping):
            raise LLMExecutionError("LLM 응답 형식이 올바르지 않습니다.")
        return dict(response)


def create_openai_transport(model: str, agents_config: Mapping[str, object]) -> LLMTransport:
    """OpenAI Responses API를 사용하는 Planner·Critic 전송 함수를 만든다.

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

        client = AsyncOpenAI()
        try:
            response = await client.responses.create(
                model=model,
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
        return parsed

    return transport


_OUTPUT_SCHEMAS: dict[str, dict[str, object]] = {
    "event_planner_agent": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "target_user",
            "duration",
            "event_concept",
            "operation_summary",
            "missions",
            "rewards",
        ],
        "properties": {
            "target_user": {"type": "string", "enum": ["new_user", "returning_user", "all_user"]},
            "duration": {"type": "integer", "minimum": 1, "maximum": 7},
            "event_concept": {"type": "string"},
            "operation_summary": {"type": "string"},
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
        "required": ["status", "error_type", "reason"],
        "properties": {
            "status": {"type": "string", "enum": ["PASS", "RETRY", "FAIL"]},
            "error_type": {"type": ["string", "null"]},
            "reason": {"type": "string"},
        },
    },
}
