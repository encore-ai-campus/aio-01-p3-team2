"""Explicit live experiment contracts and conservative persistent call reservations."""

import hashlib
import json
import os
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from backend.app.schemas.planning import EventBriefInput, EventPlan
from backend.app.services.llm_service import LLMBudgetExceeded
from backend.tests.support.fault_lab_runner import brief_fixture

CASES = {
    "normal_single": {"kind": "normal", "terminal": "WAITING_APPROVAL"},
    "normal_multi": {"kind": "normal", "terminal": "WAITING_APPROVAL"},
    "normal_unsupported": {"kind": "normal", "terminal": "COMPLETED"},
    "planner_format_once": {"kind": "injection", "scenario_id": "planner_output_invalid"},
    "planner_explanation_once": {
        "kind": "injection",
        "scenario_id": "planner_explanation_mismatch",
    },
    "llm_timeout_once": {"kind": "injection", "scenario_id": "llm_timeout"},
    "planner_format_always": {
        "kind": "injection",
        "scenario_id": "planner_output_invalid",
        "pattern": "always",
        "terminal": "SAFE_STOP",
    },
    "llm_timeout_always": {
        "kind": "injection",
        "scenario_id": "llm_timeout",
        "pattern": "always",
        "terminal": "SAFE_STOP",
    },
    "mcp_disconnect_once": {"kind": "wire", "mode": "disconnect_once"},
    "mcp_delay_once": {"kind": "wire", "mode": "delay_once"},
    "mcp_503_once": {"kind": "wire", "mode": "http503_once"},
    "mcp_503_always": {"kind": "wire", "mode": "http503_always", "terminal": "SAFE_STOP"},
}


def fixture(case_id: str) -> dict:
    case = CASES[case_id]
    brief = deepcopy(brief_fixture())
    if case_id == "normal_multi":
        brief["actions"].append(deepcopy(brief["actions"][0]))
        brief["actions"][1]["content_scope"]["content_ids"] = ["rift_space_02"]
        brief["rewards"].append({"item_id": "GOLD", "quantity": 30})
        brief["mission_reward_links"].append({"action_index": 1, "reward_index": 1})
    if case_id == "normal_unsupported":
        brief["venue"]["capability_id"] = "venue.future_boss_room"
    assert case["kind"] in {"normal", "injection", "wire"}
    return brief


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class CampaignBudget:
    """Reserve worst configured token charge before each real provider attempt.

    Uncertain failures retain reservations. Actual usage is reported separately;
    configured prices do not certify the provider invoice or future prices.
    Caller must hold the shared lab lease across ledger updates and execution.
    """

    def __init__(self, path: Path, *, max_calls: int = 90, max_cost: str = "3.00"):
        self.path = path
        self.input_limit = int(os.environ["LLM_MAX_INPUT_TOKENS"])
        self.output_limit = int(os.environ["LLM_MAX_OUTPUT_TOKENS"])
        self.input_price = Decimal(os.environ["LLM_INPUT_USD_PER_MILLION"])
        self.output_price = Decimal(os.environ["LLM_OUTPUT_USD_PER_MILLION"])
        ceiling = Decimal(max_cost)
        if (
            type(max_calls) is not int
            or not 1 <= max_calls <= 100
            or not ceiling.is_finite()
            or not 0 < ceiling <= 3
            or min(self.input_limit, self.output_limit) < 1
            or any(not x.is_finite() or x <= 0 for x in [self.input_price, self.output_price])
        ):
            raise ValueError("INVALID_EXPERIMENT_BUDGET")
        self.unit_reservation = (
            self.input_limit * self.input_price + self.output_limit * self.output_price
        ) / 1_000_000
        self.identity = {
            "model": os.environ["OPENAI_MODEL"],
            "pricing_version": os.environ["LLM_PRICING_VERSION"],
            "input_limit": self.input_limit,
            "output_limit": self.output_limit,
            "input_price": str(self.input_price),
            "output_price": str(self.output_price),
            "max_calls": max_calls,
            "max_cost": str(ceiling),
            "max_reserved_input_tokens": max_calls * self.input_limit,
            "max_reserved_output_tokens": max_calls * self.output_limit,
        }
        self.data = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else {
                "identity": self.identity,
                "calls_reserved": 0,
                "cost_reserved": "0",
                "attempts": [],
            }
        )
        if self.data["identity"] != self.identity:
            raise ValueError("CAMPAIGN_CONFIGURATION_CHANGED")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    async def invoke(self, transport, agent, context, *, instructions: str, schema: dict):
        # A byte bound conservatively exceeds token counts for the JSON input.
        # Include instructions/schema and ample protocol framing; this is not a token measurement.
        byte_bound = (
            len(json.dumps(context, ensure_ascii=False).encode())
            + len(instructions.encode())
            + len(json.dumps(schema).encode())
            + 4096
        )
        if byte_bound > self.input_limit:
            raise LLMBudgetExceeded("EXPERIMENT_INPUT_RESERVATION_EXCEEDED")
        new_cost = Decimal(self.data["cost_reserved"]) + self.unit_reservation
        if self.data.get("blocked"):
            raise LLMBudgetExceeded("EXPERIMENT_CAMPAIGN_BLOCKED")
        if self.data["calls_reserved"] >= self.identity["max_calls"] or new_cost > Decimal(
            self.identity["max_cost"]
        ):
            raise LLMBudgetExceeded("EXPERIMENT_CAMPAIGN_BUDGET_EXCEEDED")
        row = {"agent": agent, "context_digest": digest(context), "status": "RESERVED"}
        self.data["calls_reserved"] += 1
        self.data["cost_reserved"] = str(new_cost)
        self.data["attempts"].append(row)
        self.save()  # Before traffic, including attempts with unknown billing outcome.
        try:
            result = await transport(agent, context)
            usage = result.get("__llm_usage__", {})
            inputs, outputs = usage.get("input_tokens"), usage.get("output_tokens")
            if type(inputs) is not int or type(outputs) is not int or min(inputs, outputs) < 0:
                raise LLMBudgetExceeded("EXPERIMENT_USAGE_UNAVAILABLE")
            cost = (inputs * self.input_price + outputs * self.output_price) / 1_000_000
            row.update(
                status="MEASURED", input_tokens=inputs, output_tokens=outputs, cost=str(cost)
            )
            if (
                inputs > self.input_limit
                or outputs > self.output_limit
                or cost > self.unit_reservation
            ):
                self.data["blocked"] = "USAGE_EXCEEDS_RESERVATION"
                raise LLMBudgetExceeded("EXPERIMENT_USAGE_EXCEEDS_RESERVATION")
            return result
        except BaseException:
            if row["status"] == "RESERVED":
                row["status"] = "USAGE_UNKNOWN"
            raise
        finally:
            self.save()


def validate_result(case_id: str, source: dict, workflow: dict) -> list[dict]:
    """Business contracts, independent of prose wording or the model's own claims."""
    source = EventBriefInput.model_validate(source).model_dump(mode="json")
    expected_terminal = CASES[case_id].get("terminal", "WAITING_APPROVAL")
    brief = workflow.get("brief", {})
    checks = []

    def check(name, expected, actual):
        checks.append(
            {"name": name, "expected": expected, "actual": actual, "passed": expected == actual}
        )

    check("terminal", expected_terminal, workflow.get("workflow_status"))
    if "plan" not in workflow:
        check("final_result", expected_terminal == "SAFE_STOP", True)
        return checks
    for field in [
        "objective",
        "target_segments",
        "event_type",
        "operation_mode",
        "schedule",
        "venue",
        "source_game_version",
        "impact_targets",
    ]:
        check("fixed_" + field, source[field], brief.get(field))
    for field, id_field in [("actions", "action_id"), ("rewards", "reward_id")]:
        check(
            "fixed_" + field,
            [{k: v for k, v in row.items() if k != id_field} for row in source[field]],
            [{k: v for k, v in row.items() if k != id_field} for row in brief.get(field, [])],
        )
    actions = [a["action_id"] for a in brief.get("actions", [])]
    rewards = [r["reward_id"] for r in brief.get("rewards", [])]
    links = [
        {
            "action_index": actions.index(r["action_id"]),
            "reward_index": rewards.index(r["reward_id"]),
        }
        for r in brief.get("mission_reward_links", [])
    ]
    expected_links = [
        {k: r[k] for k in ["action_index", "reward_index"]} for r in source["mission_reward_links"]
    ]
    check("fixed_reward_links", expected_links, links)
    plan = workflow["plan"]
    try:
        EventPlan.model_validate(plan)
        valid_schema = True
    except ValueError:
        valid_schema = False
    check("plan_schema", True, valid_schema)
    for field in ["objective", "event_type", "operation_mode", "schedule"]:
        check("plan_" + field, source[field], plan.get(field))
    planned_actions = {m.get("source_action_id"): m for m in plan.get("missions", [])}
    for action in brief.get("actions", []):
        mission = planned_actions.get(action["action_id"], {})
        for field in ["action_type", "goal", "content_scope", "filters"]:
            check("mission_" + field, action.get(field), mission.get(field))
        check(
            "mission_reset_period",
            "daily" if action["goal"]["aggregation"] == "count" else "once",
            mission.get("reset_period"),
        )
    check("mission_count", len(brief.get("actions", [])), len(plan.get("missions", [])))
    expected_rewards = []
    for link in brief.get("mission_reward_links", []):
        reward = next(r for r in brief["rewards"] if r["reward_id"] == link["reward_id"])
        mission = planned_actions.get(link["action_id"], {})
        expected_rewards.append(
            {
                "mission_id": mission.get("mission_id"),
                **{
                    k: reward[k]
                    for k in ["item_id", "quantity", "grant_policy", "max_grants_per_user"]
                },
            }
        )
    actual_rewards = [
        {
            k: r.get(k)
            for k in ["mission_id", "item_id", "quantity", "grant_policy", "max_grants_per_user"]
        }
        for r in plan.get("rewards", [])
    ]
    check("plan_rewards", expected_rewards, actual_rewards)
    for item_id in {r["item_id"] for r in source["rewards"]}:
        expected = sum(
            r["quantity"] * (r["max_grants_per_user"] or 1)
            for r in source["rewards"]
            if r["item_id"] == item_id
        )
        impact = next(
            (
                v
                for v in plan.get("impact_assessment", [])
                if v.get("metric") == "per_user_reward_ceiling" and v.get("unit") == item_id
            ),
            {},
        )
        check("calculated_reward_ceiling", expected, impact.get("value"))
        check(
            "reward_calculation_evidence",
            True,
            impact.get("status") == "calculated"
            and bool(impact.get("evidence_refs"))
            and bool(impact.get("method_ref")),
        )
    if case_id == "normal_unsupported":
        check("runtime_blocked", False, plan.get("runtime_eligible"))
        check("development_required", True, bool(plan.get("development_requirements")))
    else:
        check("runtime_eligible", True, plan.get("runtime_eligible"))
        check("plan_validated", "VALIDATED", plan.get("plan_status"))
    check(
        "planner_summary",
        True,
        bool(workflow.get("agent_analysis", {}).get("planner", {}).get("summary")),
    )
    return checks
