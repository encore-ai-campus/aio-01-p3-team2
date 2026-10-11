"""Preparation checks use local transports; never call a paid provider."""

import asyncio
import json
import socket
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

import httpx
import pytest
from backend.app.schemas.planning import EventBriefInput
from backend.app.services.canonical_planning import create_event_workflow
from backend.app.services.llm_service import LLMBudgetExceeded
from backend.tests.support.live_recovery import CASES, CampaignBudget, fixture, validate_result
from backend.tests.support.mcp_fault_proxy import fault_proxy


@pytest.fixture
def configured(monkeypatch):
    values = {
        "LLM_MAX_INPUT_TOKENS": "10000",
        "LLM_MAX_OUTPUT_TOKENS": "1000",
        "LLM_INPUT_USD_PER_MILLION": "1",
        "LLM_OUTPUT_USD_PER_MILLION": "2",
        "OPENAI_MODEL": "unit-test-model",
        "LLM_PRICING_VERSION": "unit-test-price",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DATABASE_URL", raising=False)


async def measured(_agent, _context):
    return {"summary": "unit fixture", "__llm_usage__": {"input_tokens": 10, "output_tokens": 5}}


def invoke(budget, transport=measured):
    return asyncio.run(budget.invoke(transport, "planner", {}, instructions="test", schema={}))


def test_reservation_persists_before_transport_and_blocks_replay(tmp_path, configured):
    path = tmp_path / "ledger.json"
    budget = CampaignBudget(path, max_calls=1)

    async def observed(agent, context):
        row = json.loads(path.read_text())
        assert row["calls_reserved"] == 1
        assert row["attempts"][0]["status"] == "RESERVED"
        return await measured(agent, context)

    invoke(budget, observed)
    restored = CampaignBudget(path, max_calls=1)
    assert restored.data["attempts"][0]["status"] == "MEASURED"
    assert restored.identity["max_reserved_input_tokens"] == 10000
    with pytest.raises(LLMBudgetExceeded):
        invoke(restored)
    assert restored.data["calls_reserved"] == 1


def test_cost_limit_rejects_before_traffic(tmp_path, configured):
    budget = CampaignBudget(tmp_path / "ledger.json", max_cost="0.001")
    with pytest.raises(LLMBudgetExceeded):
        invoke(budget)
    assert not budget.path.exists()


def test_unknown_usage_keeps_reservation(tmp_path, configured):
    budget = CampaignBudget(tmp_path / "ledger.json")

    async def broken(_agent, _context):
        raise TimeoutError("unit timeout")

    with pytest.raises(TimeoutError):
        invoke(budget, broken)
    assert budget.data["cost_reserved"] == "0.012"
    assert budget.data["attempts"][0]["status"] == "USAGE_UNKNOWN"


def test_over_usage_blocks_further_calls(tmp_path, configured):
    budget = CampaignBudget(tmp_path / "ledger.json")

    async def excessive(_agent, _context):
        return {"__llm_usage__": {"input_tokens": 10001, "output_tokens": 1}}

    with pytest.raises(LLMBudgetExceeded):
        invoke(budget, excessive)
    with pytest.raises(LLMBudgetExceeded, match="CAMPAIGN_BLOCKED"):
        invoke(budget)
    assert budget.data["calls_reserved"] == 1


def test_configuration_change_rejected(tmp_path, configured, monkeypatch):
    budget = CampaignBudget(tmp_path / "ledger.json")
    invoke(budget)
    monkeypatch.setenv("OPENAI_MODEL", "changed")
    with pytest.raises(ValueError, match="CONFIGURATION_CHANGED"):
        CampaignBudget(budget.path)


@pytest.mark.parametrize("case", list(CASES))
def test_whitelisted_fixtures_validate(case):
    EventBriefInput.model_validate(fixture(case))


@pytest.mark.parametrize("case", ["normal_single", "normal_multi", "normal_unsupported"])
def test_result_contract_against_local_canonical_workflow(case, configured):
    source = fixture(case)
    result = create_event_workflow(EventBriefInput.model_validate(source), "unit-live-" + case)
    checks = validate_result(case, source, result)
    assert all(c["passed"] for c in checks), [
        (c["name"], c["actual"]) for c in checks if not c["passed"]
    ]
    tampered = deepcopy(result)
    tampered["plan"]["rewards"][0]["quantity"] += 1
    assert any(not c["passed"] for c in validate_result(case, source, tampered))


@pytest.fixture
def upstream():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            content = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize(
    "mode", ["http503_once", "disconnect_once", "delay_once", "http503_always"]
)
def test_owned_proxy_real_http_faults_and_shutdown(upstream, mode):
    with fault_proxy(upstream, mode, delay_seconds=0.2) as (endpoint, journal):
        with httpx.Client(timeout=2, trust_env=False) as client:
            if mode in {"disconnect_once", "delay_once"}:
                with pytest.raises(httpx.TransportError):
                    client.post(endpoint, content=b"probe", timeout=0.1)
            else:
                assert client.post(endpoint, content=b"probe").status_code == 503
            response = client.post(endpoint, content=b"probe", timeout=2)
            assert response.status_code == (503 if mode == "http503_always" else 200)
            if mode != "http503_always":
                assert response.content == b"probe"
            assert any("wire_fault" in row for row in journal)
            assert client.post(endpoint + "/wrong").status_code == 404
        port = urlsplit(endpoint).port
    with socket.socket() as connection:
        assert connection.connect_ex(("127.0.0.1", port)) != 0


@pytest.mark.parametrize(
    "url",
    [
        "http://remote/mcp",
        "https://127.0.0.1/mcp",
        "http://user:secret@127.0.0.1/mcp",
        "http://127.0.0.1/other",
    ],
)
def test_proxy_rejects_non_owned_boundaries(url):
    with pytest.raises(ValueError), fault_proxy(url, "http503_once"):
        pytest.fail("must not start")


def test_cli_default_prepares_without_execute(monkeypatch, tmp_path, configured):
    from backend.scripts import live_recovery_experiment as cli

    monkeypatch.setattr(cli, "load_dotenv", lambda *_a, **_kw: None)
    monkeypatch.setattr("sys.argv", ["live-preparation"])
    monkeypatch.setattr(cli, "OUTPUT", tmp_path)
    monkeypatch.setattr(
        cli,
        "prepare",
        lambda: {
            "status": "PREPARED_NOT_EXECUTED",
            "policy_version": 1,
            "policy_digest": cli.digest({}),
            "runtime_digest": cli.digest({}),
        },
    )
    monkeypatch.setattr(
        cli.policies,
        "list_policies",
        lambda: {"versions": [{"status": "ACTIVE", "version": 1, "policy": {}}]},
    )
    monkeypatch.setattr(cli.store, "environment", lambda: {"state": "CLEAN"})
    monkeypatch.setattr(cli, "runtime_snapshot", dict)
    monkeypatch.setattr(
        cli, "execute", lambda *_a, **_kw: pytest.fail("unexpected provider execution")
    )
    assert cli.main() == 0
    assert len(list(tmp_path.glob("preflight-*.json"))) == 1


def test_cli_execute_requires_explicit_flags(monkeypatch):
    from backend.scripts import live_recovery_experiment as cli

    monkeypatch.setattr(cli, "load_dotenv", lambda *_a, **_kw: None)
    monkeypatch.setattr("sys.argv", ["live-preparation", "--execute"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


def test_live_format_injection_preserves_provider_evidence(monkeypatch):
    from backend.app.fault_injection.injector import injection_context, perturb

    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
    monkeypatch.setenv("FAULT_LAB_MODE", "isolated")
    original = {
        "summary": "unit",
        "__llm_usage__": {"input_tokens": 10},
        "__llm_record__": {"response": "unit raw response"},
    }
    with injection_context(
        "unit",
        {"scenario_id": "planner_output_invalid", "pattern": "once", "first_n": 1},
        "planner.response",
        lambda _r: None,
    ):
        mutated = perturb("planner.response", original)
        assert mutated["invalid_fault_lab_contract"] is True
        assert mutated["__llm_usage__"] == original["__llm_usage__"]
        assert mutated["__llm_record__"] == original["__llm_record__"]
        assert "summary" not in mutated
        assert perturb("planner.response", original) == original


def test_live_service_records_original_before_validating_mutation(monkeypatch, configured):
    from backend.app.fault_injection.injector import injection_context
    from backend.app.services.llm_service import (
        LLMService,
        workflow_llm_call_budget,
        workflow_llm_usage_snapshot,
    )

    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
    monkeypatch.setenv("FAULT_LAB_MODE", "isolated")
    monkeypatch.setenv("CANONICAL_PLANNING_MODE", "live")
    monkeypatch.setenv("LLM_MAX_COST_USD", "1")
    monkeypatch.setattr("backend.app.services.llm_service.validate_live_recording", lambda: None)
    written = []

    def record(**values):
        written.append(values)
        return {"record_id": "unit", "prompt_hash": "unit"}

    monkeypatch.setattr("backend.app.services.llm_service.write_live_record", record)

    async def transport(_agent, _context):
        return {
            "summary": "unit",
            "__llm_usage__": {"input_tokens": 10, "output_tokens": 1},
            "__llm_record__": {
                "model_id": "unit",
                "prompt_template_version": "unit",
                "prompt": "unit prompt",
                "response": "unit original response",
            },
        }

    async def run():
        with (
            workflow_llm_call_budget(),
            injection_context(
                "unit",
                {"scenario_id": "planner_output_invalid", "pattern": "once", "first_n": 1},
                "planner.response",
                lambda _r: None,
            ),
        ):
            result = await LLMService(transport, 1, source="live").generate(
                "canonical_event_planner_agent", {}
            )
            assert result == {"invalid_fault_lab_contract": True}
            assert workflow_llm_usage_snapshot()["input_tokens"] == 10

    asyncio.run(run())
    assert len(written) == 1
    assert written[0]["response"] == "unit original response"
