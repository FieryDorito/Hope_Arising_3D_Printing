from __future__ import annotations

import importlib

from ..config import settings
from .base import Agent, AgentError, Suggestion

PROVIDERS = {
    "claude": "pipeline.agents.claude_agent:ClaudeAgent",
    # "local": "pipeline.agents.local_agent:LocalAgent",   # e.g. an offline model later
}


def get_agent() -> Agent:
    """Return the configured agent, or raise AgentError (UI then falls back to manual mode)."""
    provider = settings()["agent"].get("provider", "none")
    if provider in (None, "", "none"):
        raise AgentError("AI helper is turned off. Use 'Browse parts'.")
    target = PROVIDERS.get(provider)
    if not target:
        raise AgentError(f"Unknown AI provider '{provider}' in settings.yaml.")
    mod, cls = target.split(":")
    return getattr(importlib.import_module(mod), cls)()


__all__ = ["get_agent", "Agent", "AgentError", "Suggestion"]
