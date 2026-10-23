"""AI agent (with a fake Claude client, no API key needed) and sheet sync."""
from types import SimpleNamespace

from pipeline import library
from pipeline.agents.claude_agent import ClaudeAgent
from pipeline.validate import check_request


class FakeClient:
    def __init__(self, answer):
        self.answer, self.calls = answer, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=self.answer)])


def test_claude_agent_returns_structured_suggestion(test_library):
    fake = FakeClient({"part_id": "test-spacer", "params": {"outer_diameter": 25, "inner_diameter": 6, "height": 4},
                       "material": "PETG", "strength": "Normal", "explanation": "A spacer fits."})
    s = ClaudeAgent(client=fake).suggest("washer 25mm wide, 6mm hole, 4mm thick", test_library)
    assert s.part_id == "test-spacer" and s.params["inner_diameter"] == 6
    call = fake.calls[0]
    assert call["tool_choice"]["name"] == "select_part"
    ids = call["tools"][0]["input_schema"]["properties"]["part_id"]["enum"]
    assert "locked-part" not in ids and "no-file-yet" not in ids  # only printable parts offered


def test_bad_ai_values_are_caught(test_library):
    fake = FakeClient({"part_id": "test-spacer", "params": {"outer_diameter": 999},
                       "material": "PLA", "strength": "Normal", "explanation": ""})
    s = ClaudeAgent(client=fake).suggest("huge washer", test_library)
    part = library.get_part(test_library, s.part_id)
    assert not check_request(part, s.params, s.material, s.strength).ok


def test_sync_keeps_old_copy_when_sheet_private(tmp_path, monkeypatch):
    target = tmp_path / "catalog.csv"
    target.write_text("part_name\nOld Part\n", encoding="utf-8")
    monkeypatch.setitem(library.settings()["library"], "catalog_csv", str(target))
    resp = SimpleNamespace(content=b"<html>sign in</html>", raise_for_status=lambda: None)
    monkeypatch.setattr(library.requests, "get", lambda *a, **k: resp)
    ok, msg = library.sync_from_sheet()
    assert not ok and "shared" in msg
    assert "Old Part" in target.read_text()


def test_sync_updates(tmp_path, monkeypatch):
    target = tmp_path / "catalog.csv"
    target.write_text("part_name\nOld Part\n", encoding="utf-8")
    monkeypatch.setitem(library.settings()["library"], "catalog_csv", str(target))
    resp = SimpleNamespace(content=b"part_name,category\nNew A,Plumbing\nNew B,Utility\n",
                           raise_for_status=lambda: None)
    monkeypatch.setattr(library.requests, "get", lambda *a, **k: resp)
    ok, msg = library.sync_from_sheet()
    assert ok and "2 parts" in msg and "New A" in target.read_text()
    assert (tmp_path / "catalog.csv.bak").exists()
