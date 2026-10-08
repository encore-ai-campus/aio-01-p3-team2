"""The direct backend.app.main entry point selects live mode safely."""

import os

import pytest
from backend.app.main import configure_live_environment


def test_main_loads_env_without_replacing_process_values(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "WORKFLOW_ENGINE=mock\n"
        "MCP_URL=http://127.0.0.1:8010/mcp\n"
        "OPENAI_MODEL=model-from-file\n"
        "OPENAI_API_KEY=key-from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_MODEL", "model-from-shell")
    monkeypatch.delenv("MCP_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    configure_live_environment(env_path)

    assert os.environ["WORKFLOW_ENGINE"] == "backend_b_live"
    assert os.environ["OPENAI_MODEL"] == "model-from-shell"
    assert os.environ["MCP_URL"] == "http://127.0.0.1:8010/mcp"
    assert os.environ["OPENAI_API_KEY"] == "key-from-file"


def test_main_rejects_missing_key(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "MCP_URL=http://127.0.0.1:8010/mcp\nOPENAI_MODEL=test-model\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        configure_live_environment(env_path)
