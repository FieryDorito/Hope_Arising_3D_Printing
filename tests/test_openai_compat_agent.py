"""Free/OpenAI-compatible agent (Gemini, Ollama, ...) with a fake HTTP call, no key needed."""
import json
from types import SimpleNamespace

import pytest

from pipeline import agents
from pipeline.agents import AgentError, get_agent
from pipeline.agents.claude_agent import ClaudeAgent
from pipeline.agents.openai_compat_agent import OpenAICompatAgent

CFG = {"type": "openai_compatible", "label": "Gemini", "base_url": "https://example.test/v1",
       "model": "test-model", "api_key": "k"}


def fake_post(status=200, message=None, text=""):
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return SimpleNamespace(status_code=status, text=text,
                               json=lambda: {"choices": [{"message": message or {}}]})
    post.calls = calls
    return post


def tool_message(args: dict) -> dict:
    return {"tool_calls": [{"type": "function",
                            "function": {"name": "select_part", "arguments": json.dumps(args)}}]}


def test_tool_call_answer(test_library):
    post = fake_post(message=tool_message({
        "part_id": "test-spacer",
        "settings": [{"name": "outer_diameter", "value": 25}, {"name": "height", "value": 4}],
        "material": "PLA", "strength": "Normal", "explanation": "Spacer fits."}))
    s = OpenAICompatAgent(CFG, post=post).suggest("25mm washer 4mm thick", test_library)
    assert s.part_id == "test-spacer" and s.params == {"outer_diameter": 25.0, "height": 4.0}
    req = post.calls[0]
    assert req["url"] == "https://example.test/v1/chat/completions"
    assert req["headers"]["Authorization"] == "Bearer k"
    ids = req["json"]["tools"][0]["function"]["parameters"]["properties"]["part_id"]["enum"]
    assert "test-spacer" in ids and "locked-part" not in ids and "NONE" in ids


def test_plain_json_fallback_and_no_match(test_library):
    msg = {"content": 'Sure! {"part_id": "NONE", "settings": [], "material": "PLA", '
                      '"strength": "Normal", "explanation": "Nothing fits."}'}
    s = OpenAICompatAgent(CFG, post=fake_post(message=msg)).suggest("a car engine", test_library)
    assert s.part_id is None and "Nothing" in s.explanation


@pytest.mark.parametrize("status,text,word", [(429, "", "limit"), (401, "", "key"),
                                              (400, "API key not valid", "key"), (500, "", "error")])
def test_errors_are_plain_language(test_library, status, text, word):
    with pytest.raises(AgentError, match=word):
        OpenAICompatAgent(CFG, post=fake_post(status=status, text=text)).suggest("hook", test_library)


def test_missing_key_and_ollama_needs_none(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(AgentError, match="no API key"):
        OpenAICompatAgent({**CFG, "api_key": "", "api_key_env": "GEMINI_API_KEY"})
    OpenAICompatAgent({**CFG, "api_key": "", "needs_key": False})  # Ollama: fine without a key


def test_provider_switch(monkeypatch):
    a = agents.settings()["agent"]
    monkeypatch.setitem(a, "providers", {**a["providers"],
                                         "gemini": {**a["providers"]["gemini"], "api_key": "x"},
                                         "claude": {**a["providers"]["claude"], "api_key": "y"}})
    monkeypatch.setitem(a, "provider", "gemini")
    assert isinstance(get_agent(), OpenAICompatAgent)
    monkeypatch.setitem(a, "provider", "claude")
    assert isinstance(get_agent(), ClaudeAgent)
    monkeypatch.setitem(a, "provider", "none")
    with pytest.raises(AgentError, match="turned off"):
        get_agent()


def test_key_at_top_level_of_agent_is_used(monkeypatch):
    a = agents.settings()["agent"]
    monkeypatch.setitem(a, "api_key", "AIzaTopLevel")
    agent = OpenAICompatAgent({**CFG, "api_key": ""})
    assert agent.key == "AIzaTopLevel"


def test_example_placeholder_is_not_a_key(monkeypatch):
    monkeypatch.delitem(agents.settings()["agent"], "api_key", raising=False)
    with pytest.raises(AgentError, match="settings.local"):
        OpenAICompatAgent({**CFG, "api_key": "paste-your-Gemini-key-here"})
