"""Claude (Anthropic API) agent.

Uses forced tool use, so Claude must answer with structured JSON matching
SELECT_TOOL's schema instead of free text. The part_id is restricted to an enum
of printable parts, so Claude can't name a part that doesn't exist.
"""
from __future__ import annotations

import os

from ..config import settings
from ..library import Part
from .base import SYSTEM_PROMPT, Agent, AgentError, Suggestion, library_brief


def _tool(parts: list[Part]) -> dict:
    s = settings()
    ids = [p.part_id for p in parts if p.printable]
    return {
        "name": "select_part",
        "description": "Choose a library part and its settings for the customer's request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "part_id": {"type": ["string", "null"], "enum": ids + [None]},
                "params": {"type": "object", "description": "setting name -> number",
                           "additionalProperties": {"type": "number"}},
                "material": {"type": "string", "enum": list(s["materials"])},
                "strength": {"type": "string", "enum": list(s["strength"])},
                "explanation": {"type": "string"},
                "question_for_customer": {"type": "string"},
            },
            "required": ["part_id", "params", "material", "strength", "explanation"],
        },
    }


class ClaudeAgent(Agent):
    name = "claude"

    def __init__(self, cfg: dict | None = None, client=None):
        top = settings()["agent"]
        cfg = cfg if cfg is not None else top.get("providers", {}).get("claude", {})
        self.model = cfg.get("model", "claude-sonnet-5-5")
        self.max_tokens = cfg.get("max_tokens", top.get("max_tokens", 1024))
        if client is not None:          # tests pass a fake client
            self.client = client
            return
        key = (cfg.get("api_key") or top.get("api_key")
               or os.environ.get(cfg.get("api_key_env", "ANTHROPIC_API_KEY"), ""))
        if not key:
            raise AgentError("AI helper is not set up (no API key). Use 'Browse parts' instead.")
        import anthropic

        self.client = anthropic.Anthropic(api_key=key, timeout=top.get("timeout_s", 60), max_retries=2)

    def suggest(self, request: str, parts: list[Part]) -> Suggestion:
        if not any(p.printable for p in parts):
            raise AgentError("No parts in the library are ready to print yet.")
        tool = _tool(parts)
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT + "\n\nShop library and options:\n" + library_brief(parts),
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": request}],
            )
        except Exception as e:  # network, auth, rate limit, ...
            raise AgentError(f"AI helper could not be reached ({type(e).__name__}). "
                             "Use 'Browse parts' instead.") from e

        block = next((b for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if block is None:
            raise AgentError("AI helper gave no answer. Use 'Browse parts' instead.")
        data = dict(block.input)
        return Suggestion(
            part_id=data.get("part_id"),
            params=data.get("params") or {},
            material=data.get("material"),
            strength=data.get("strength"),
            explanation=data.get("explanation", ""),
            question=data.get("question_for_customer", "") or "",
            raw=data,
        )
