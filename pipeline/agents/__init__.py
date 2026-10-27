from __future__ import annotations

import importlib

from ..config import settings
from .base import Agent, AgentError, Suggestion

# Agent "type" in settings.yaml -> class. Add new kinds of AI service here.
TYPES = {
    "claude": "pipeline.agents.claude_agent:ClaudeAgent",
    "openai_compatible": "pipeline.agents.openai_compat_agent:OpenAICompatAgent",
}


def get_agent() -> Agent:
    """Return the agent picked by `agent.provider` in settings.yaml.

    Raises AgentError when it's off or not set up; the UI then falls back to manual mode.
    """
    cfg = settings()["agent"]
    provider = cfg.get("provider", "none")
    if provider in (None, "", "none"):
        raise AgentError("AI helper is turned off. Use 'Browse parts'.")
    block = (cfg.get("providers") or {}).get(provider)
    if block is None:
        raise AgentError(f"Unknown AI provider '{provider}' in settings.yaml.")
    target = TYPES.get(block.get("type", provider))
    if not target:
        raise AgentError(f"Unknown AI type '{block.get('type')}' for provider '{provider}'.")
    mod, cls = target.split(":")
    return getattr(importlib.import_module(mod), cls)(block)


__all__ = ["get_agent", "Agent", "AgentError", "Suggestion"]
