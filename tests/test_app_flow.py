"""Click through the operator screens like a shop worker would (no browser needed).

Find part -> change a size -> check -> go back and forth -> print file -> save to card
-> customer details -> Past prints -> change and print again.
"""
import json
import shutil
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from pipeline import drives, history, sheet

APP = str(Path(__file__).resolve().parent.parent / "ui" / "app.py")
PART = "spacer"            # models/Household/spacer (adjustable OpenSCAD part)
SIZE_KEY = f"p_{PART}_outer_diameter"

needs_tools = pytest.mark.skipif(not (shutil.which("openscad") and
                                      (shutil.which("prusa-slicer") or shutil.which("prusa-slicer-console"))),
                                 reason="needs OpenSCAD and PrusaSlicer")


@pytest.fixture
def app(monkeypatch, tmp_path):
    # never touch the real Google Sheet or the real cards plugged into this computer
    monkeypatch.setattr(sheet, "check_for_updates", lambda *a, **k: sheet.SheetUpdate(True, "Library is up to date."))
    card = tmp_path / "SDCARD"
    card.mkdir()
    monkeypatch.setattr(drives, "list_removable", lambda: [drives.Drive(card, "SDCARD")])
    at = AppTest.from_file(APP, default_timeout=300)
    at.card = card
    return at.run()


def button(at, label_start):
    hits = [b for b in at.button if b.label.startswith(label_start)]
    assert hits, f"no button starting with {label_start!r}: {[b.label for b in at.button]}"
    return hits[0]


def test_start_screen_and_help(app):
    assert not app.exception
    assert app.session_state.step == 1
    button(app, "❓ How to use").click().run()
    assert not app.exception
    assert any("Making a print" in m.value for m in app.markdown)


@needs_tools
def test_full_order_flow(app):
    at = app
    at.button(key=f"choose_{PART}").click().run()
    assert at.session_state.step == 2 and not at.exception

    at.number_input(key=SIZE_KEY).set_value(30.0).run()
    button(at, "Next: check the part").click().run()
    assert not at.exception and at.session_state.step == 3, [e.value for e in at.error]

    # back to step 2 and forward again via the sidebar: the size must not be lost
    at.button(key="nav_2").click().run()
    assert at.session_state.step == 2 and at.number_input(key=SIZE_KEY).value == 30.0
    assert not at.button(key="nav_3").disabled          # nothing changed, so the preview is still valid
    at.button(key="nav_3").click().run()
    assert at.session_state.step == 3

    # changing a size makes the old preview out of date
    at.button(key="nav_2").click().run()
    at.number_input(key=SIZE_KEY).set_value(31.0).run()
    assert at.button(key="nav_3").disabled
    at.number_input(key=SIZE_KEY).set_value(30.0).run()

    at.button(key="nav_3").click().run()
    button(at, "Next: make print file").click().run()
    assert not at.exception and at.session_state.step == 4, [e.value for e in at.error]

    # save to the (fake) SD card
    button(at, "💾 Save").click().run()
    assert any("Saved to SDCARD" in s.value for s in at.success)
    on_card = list(at.card.glob("*.gcode"))
    assert len(on_card) == 1 and on_card[0].read_text(encoding="utf-8").startswith(";")

    # customer details are saved next to the job
    job = at.session_state.job
    at.text_input(key=f"new_{job.folder.name}_name").input("Mulu")
    at.text_input(key=f"new_{job.folder.name}_phone").input("0911 000000")
    button(at, "Save details").click().run()
    order = json.loads((job.folder / "order.json").read_text(encoding="utf-8"))
    assert order["customer"] == "Mulu" and order["status"] == history.DEFAULT_STATUS

    # Past prints lists it; "Change and print again" reopens it at step 2 with the same size
    at.sidebar.radio(key="page").set_value("Past prints").run()
    assert not at.exception
    assert any("Mulu" in e.label for e in at.expander)
    at.button(key=f"reopen_{job.folder.name}").click().run()
    assert at.session_state.page == "Make a print" and at.session_state.step == 2
    assert at.number_input(key=SIZE_KEY).value == 30.0

    # start the next order clears everything
    at.button(key="nav_1").click().run()
    button(at, "Start a new order").click().run()
    assert at.session_state.step == 1 and at.session_state.part_id is None
