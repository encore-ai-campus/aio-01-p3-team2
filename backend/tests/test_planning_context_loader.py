import asyncio

import pytest
from backend.app.services.mcp_client import MCPTimeoutError, MCPToolError
from backend.app.services.planning_context_loader import PlanningContextLoader
from backend.app.services.workflow_service import BackendBWorkflowAdapter


def test_planning_context_loader_forwards_typed_arguments_and_envelope():
    calls = []

    async def transport(tool_name, arguments):
        calls.append((tool_name, arguments))
        return {
            "game_data_version": "tinyheroes-1.0.0",
            "source": "primary",
            "contents": [{"content_id": "HUNT_001", "required_level": 1}],
            "items": [],
            "user_segments": {},
        }

    result = asyncio.run(PlanningContextLoader(transport).load("new_user", 3))
    assert calls == [("get_event_planning_context", {"target_user": "new_user", "duration": 3})]
    assert result["contents"][0]["content_id"] == "HUNT_001"


def test_planning_context_loader_rejects_invalid_version():
    async def transport(_tool_name, _arguments):
        return {
            "contents": [],
            "items": [],
            "user_segments": {},
            "game_data_version": "",
            "source": "primary",
        }

    with pytest.raises(MCPToolError):
        asyncio.run(PlanningContextLoader(transport).load("new_user", 1))


def test_planning_context_loader_classifies_connection_failure():
    async def transport(_tool_name, _arguments):
        raise ConnectionError("offline")

    with pytest.raises(MCPTimeoutError):
        asyncio.run(PlanningContextLoader(transport).load("new_user", 1))


def test_adapter_exposes_planning_context_loader(monkeypatch):
    async def transport(_self, _tool_name, _arguments):
        return {
            "contents": [],
            "items": [],
            "user_segments": {},
            "game_data_version": "tinyheroes-1.0.0",
            "source": "primary",
        }

    monkeypatch.setattr(BackendBWorkflowAdapter, "_mcp_transport", transport)
    adapter = BackendBWorkflowAdapter()
    context = asyncio.run(adapter.planning_context_loader("new_user", 1))
    assert context["game_data_version"] == "tinyheroes-1.0.0"
