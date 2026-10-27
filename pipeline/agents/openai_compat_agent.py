"""Agent for any service that speaks the OpenAI "chat completions" format.

One class covers several providers; only base_url / model / key change in settings.yaml:
  * Google Gemini (free tier)  https://generativelanguage.googleapis.com/v1beta/openai/
  * Ollama (free, runs on the shop computer, no internet)  http://localhost:11434/v1/
  * Groq, OpenRouter, OpenAI, ...

Like the Claude agent, it must answer through the select_part tool, and its answer
still goes through validate.check_request() before anything is built.
The schema avoids features some providers reject (null in enums, free-form objects):
settings come back as a list of {name, value} pairs.
"""
from __future__ import annotations

import json
import os
import re

import requests

from ..config import settings
from ..library import Part
from .base import SYSTEM_PROMPT, Agent, AgentError, Suggestion, library_brief

NO_MATCH = "NONE"


def tool_schema(parts: list[Part]) -> dict:
    s = settings()
    ids = [p.part_id for p in parts if p.printable]
    return {
        "type": "function",
        "function": {
            "name": "select_part",
            "description": "Choose a library part and its settings for the customer's request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "part_id": {"type": "string", "enum": ids + [NO_MATCH],
                                "description": f"Library part_id, or {NO_MATCH} if nothing fits."},
                    "settings": {
                        "type": "array",
                        "description": "Values for the part's settings.",
                        "items": {"type": "object",
                                  "properties": {"name": {"type": "string"}, "value": {"type": "number"}},
                                  "required": ["name", "value"]},
                    },
                    "material": {"type": "string", "enum": list(s["materials"])},
                    "strength": {"type": "string", "enum": list(s["strength"])},
                    "explanation": {"type": "string"},
                    "question_for_customer": {"type": "string"},
                },
                "required": ["part_id", "settings", "material", "strength", "explanation"],
            },
        },
    }


def _parse_args(message: dict) -> dict:
    calls = message.get("tool_calls") or []
    if calls:
        args = calls[0].get("function", {}).get("arguments", "{}")
        return json.loads(args) if isinstance(args, str) else args
    # Some models answer with plain JSON text instead of a tool call.
    m = re.search(r"\{.*\}", message.get("content") or "", re.S)
    if m:
        return json.loads(m.group(0))
    raise ValueError("no tool call")


class OpenAICompatAgent(Agent):
    name = "openai_compatible"

    def __init__(self, cfg: dict, post=None):
        top = settings()["agent"]
        self.base_url = cfg["base_url"].rstrip("/") + "/"
        self.model = cfg["model"]
        self.timeout = top.get("timeout_s", 60)
        self.tool_choice = cfg.get("tool_choice", "auto")
        self.max_tokens = cfg.get("max_tokens")
        self.label = cfg.get("label", "AI helper")
        key_env = cfg.get("api_key_env", "")
        self.key = cfg.get("api_key") or (os.environ.get(key_env, "") if key_env else "")
        if cfg.get("needs_key", True) and not self.key:
            raise AgentError(f"{self.label} is not set up (no API key). Use 'Browse parts' instead.")
        self._post = post or requests.post

    def suggest(self, request: str, parts: list[Part]) -> Suggestion:
        if not any(p.printable for p in parts):
            raise AgentError("No parts in the library are ready to print yet.")
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT
                 + "\nAlways answer by calling the select_part function."
                 + "\n\nShop library and options:\n" + library_brief(parts)},
                {"role": "user", "content": request},
            ],
            "tools": [tool_schema(parts)],
            "tool_choice": self.tool_choice,
        }
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"

        try:
            r = self._post(self.base_url + "chat/completions", json=body, headers=headers,
                           timeout=self.timeout)
        except requests.RequestException as e:
            raise AgentError(f"{self.label} could not be reached. Use 'Browse parts' instead.") from e
        if r.status_code == 429:
            raise AgentError(f"{self.label} free limit reached for now. Wait a minute, "
                             "or use 'Browse parts'.")
        if r.status_code in (401, 403) or (r.status_code == 400 and "api key" in r.text.lower()):
            raise AgentError(f"{self.label} did not accept the API key. Check settings.local.yaml.")
        if r.status_code >= 400:
            raise AgentError(f"{self.label} returned an error ({r.status_code}). Use 'Browse parts' instead.")

        try:
            data = _parse_args(r.json()["choices"][0]["message"])
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise AgentError(f"{self.label} gave an answer the app couldn't read. "
                             "Try rewording, or use 'Browse parts'.") from e

        pid = data.get("part_id")
        params = {}
        for item in data.get("settings") or []:
            try:
                params[str(item["name"])] = float(item["value"])
            except (KeyError, TypeError, ValueError):
                continue
        return Suggestion(
            part_id=None if pid in (None, "", NO_MATCH) else pid,
            params=params,
            material=data.get("material"),
            strength=data.get("strength"),
            explanation=data.get("explanation", ""),
            question=data.get("question_for_customer", "") or "",
            raw=data,
        )
