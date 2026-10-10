"""Factory for canonical Planner/Critic agents and their explicit local adapter."""

import os
from collections.abc import Mapping
from pathlib import Path

import yaml

from backend.app.agents.canonical_critic import (
    CanonicalCritic,
    canonical_critic_response,
)
from backend.app.agents.canonical_planner import (
    CanonicalPlanner,
    canonical_planner_response,
)
from backend.app.agents.supervisor import Supervisor
from backend.app.services.llm_service import LLMService, create_openai_transport


def build_canonical_agents() -> tuple[CanonicalPlanner, CanonicalCritic, Supervisor, str]:
    """Build strict Planner/Critic boundaries in deterministic or configured live mode."""
    mode = os.getenv("CANONICAL_PLANNING_MODE", "deterministic").strip().casefold()
    config_path = Path(__file__).resolve().parents[1] / "config" / "agents.yaml"
    with config_path.open(encoding="utf-8") as config_file:
        agents_config = yaml.safe_load(config_file)
    security_path = config_path.with_name("security_policies.yaml")
    with security_path.open(encoding="utf-8") as policy_file:
        security_policy = yaml.safe_load(policy_file)

    if mode == "deterministic":

        async def transport(agent_name: str, context: dict[str, object]) -> Mapping[str, object]:
            if agent_name == CanonicalPlanner.AGENT_NAME:
                return canonical_planner_response(agent_name, context)
            if agent_name == CanonicalCritic.AGENT_NAME:
                return canonical_critic_response(agent_name, context)
            raise ValueError("Unsupported canonical agent")

        service = LLMService(transport, 30, source="mock")
        profile = "canonical_deterministic_agents"
    elif mode == "live":
        model = os.getenv("OPENAI_MODEL", "").strip()
        if not model or not os.getenv("OPENAI_API_KEY", "").strip():
            raise ValueError("canonical_planning_live requires OPENAI_MODEL and OPENAI_API_KEY")
        service = LLMService(create_openai_transport(model, agents_config), 30, source="live")
        profile = f"canonical_openai_agents:{model}"
    else:
        raise ValueError("CANONICAL_PLANNING_MODE must be deterministic or live")

    return (
        CanonicalPlanner(service),
        CanonicalCritic(service),
        Supervisor(security_policy),
        profile,
    )
