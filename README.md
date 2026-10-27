# Hope_Arising_3D_Printing

ASU EPICS × Hope Arising (Addis Ababa). A shop worker describes what a customer needs or
picks a part from the library, adjusts the size, and gets a print file (G-code) for the printer.

```
Google Sheet ─┐
              ├─► library ─► AI agent ─► validate ─► CAD ─► slicer ─► G-code
models/ ──────┘   (pipeline/library.py) (agents/)  (validate.py) (cad.py) (slicers/)
```

The UI (`ui/app.py`) only displays things. It calls `run_job.prepare()` and `run_job.make_gcode()`.
Each stage can be replaced without touching the others.

## Run it

Requirements: Python 3.13 (3.12 and 3.14 also work; 3.15 is too new for some add-ons yet), [PrusaSlicer](https://www.prusa3d.com/page/prusaslicer_424/),
and [OpenSCAD](https://openscad.org) (only needed for adjustable parts).

- **Windows:** double-click `start.bat`. The first run installs everything.
- **Linux/macOS:** `./start.sh`

To turn on the AI helper, copy `config/settings.local.example.yaml` to `config/settings.local.yaml`
and paste in a key. The default is **Gemini's free tier** (key from https://aistudio.google.com/apikey,
no credit card). Switch to Claude or an offline Ollama model by changing `agent.provider`.
Without a key, "Browse parts" still works.

## Where to change things

| Change | File |
|---|---|
| Add a part | Google Sheet + `models/<Category>/<part_id>/`. See [docs/ADDING_PARTS.md](docs/ADDING_PARTS.md) |
| Printer, bed size | `config/settings.yaml`, `config/printers/*.ini` |
| Material temps, prices | `config/filaments/*.ini`, `materials:` in settings |
| Infill / walls | `config/process/*.ini` |
| AI provider (gemini / claude / ollama) | `agent.provider` in settings; new kinds go in `pipeline/agents/` |
| Slicer (e.g. OrcaSlicer) | `slicer.backend` in settings; backends in `pipeline/slicers/` |

## Rules that keep it reliable

- The AI only **chooses a part and fills in numbers**. Every value is checked against `min`/`max` in `validate.py` before anything is built.
- `python -m pytest` slices **every** part in the library. GitHub runs the tests on every push, so don't merge changes that fail them.
- Every job is saved in `jobs/<time>_<part>/`: the request, the final settings, the STL, the G-code and the slicer log.
- Versions are pinned in `requirements.txt`. To upgrade, change the version on purpose, then run the tests.
- Parts whose license doesn't allow commercial use are blocked from printing. Each G-code file starts with a credit line for the part's designer.

## Status

- Working: library sync from the sheet, browsing and search, AI agent (Gemini free tier, Claude, or Ollama), size checks, OpenSCAD and STL parts, PrusaSlicer, preview, print time and cost.
- Not yet: real model files (only test shapes so far), OrcaSlicer backend (written but **untested**), sending files to the printer over the network.
