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
| Add or change a printer | `printers:` in `config/settings.yaml` (+ a `config/printers/*.ini` for G-code printers). See [Printers](#printers) |
| Material temps, prices | `config/filaments/*.ini`, `materials:` in settings |
| Infill / walls | `config/process/*.ini` |
| AI provider (gemini / claude / ollama) | `agent.provider` in settings; new kinds go in `pipeline/agents/` |
| Slicer | OrcaSlicer for every printer with `orca_profiles` (`pipeline/slicers/orcaslicer.py`); PrusaSlicer fallback in `slicer.backend` |
| The "How to use" guide | `ui/help/how_to_use.md` (plain text, easy to edit or translate) |
| Wi-Fi printers (experimental) | `network_printers:` in settings; code in `pipeline/netprint.py` |
| OpenSCAD in an unusual folder | `cad: openscad_executable:` in `settings.local.yaml` (normal install folders are found automatically) |

## Printers

The shop can have several printers. Each one is an entry under `printers:` in `config/settings.yaml`
(the first is the default); the worker picks one in step 2 and the part is checked against **that** bed.

**Slicing is done by [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer)** (free). It ships official
settings for most printers (Bambu Lab, Creality, Prusa, Elegoo, Anycubic, Voron and more), so adding a
printer is mostly picking names. Install OrcaSlicer on the computer that runs the app; the app finds it
on Windows, Mac and Linux (or set `slicer: orca:` in `settings.local.yaml`).

| `output:` | What the worker gets | For |
|---|---|---|
| `gcode` | A sliced `.gcode` for the SD card / Wi-Fi (Klipper) | Ender 3 and other Marlin printers, Prusa, Klipper printers |
| `bambu` | A sliced `.gcode.3mf`, sent to the printer over Wi-Fi with one button (or saved to a card / USB stick) | Bambu Lab (P2S, P1S, X1, A1, H2...) |
| `3mf` | An arranged, **not** sliced `.3mf` to slice in the printer's own software | Anything else |

If OrcaSlicer isn't installed, `gcode` printers fall back to PrusaSlicer (`profile:`, an `.ini` in
`config/printers/`) and `bambu` printers fall back to a `.3mf` to slice by hand. The app says so on screen.

### Adding a printer

1. Find the printer in OrcaSlicer's `resources/profiles/<Brand>/` folder (in the OrcaSlicer install, or
   [on GitHub](https://github.com/OrcaSlicer/OrcaSlicer/tree/main/resources/profiles)). Note the exact
   names of the printer (machine), a process (e.g. `0.20mm Standard @...`) and a PLA and PETG filament.
2. Make the app's copy of those settings:
   ```
   python tools/make_slicer_profiles.py "<OrcaSlicer>/resources/profiles" config/orca/<short-name> --vendor <Brand> \
       --machine "<printer name>" --process "<process name>" \
       --filament PLA="<PLA name>" --filament PETG="<PETG name>"
   ```
3. Copy a printer entry in `config/settings.yaml`, change the name, bed size, `output` and
   `orca_profiles: config/orca/<short-name>`. Run `python -m pytest`.

Normal / Strong change walls, infill and supports on top of the printer's own settings (`orca:` under
`strength:`). Supports are **normal**, not tree: tree supports grow sideways into the next piece and
OrcaSlicer then refuses to slice.

### Bambu Lab over Wi-Fi

On the printer: *Settings → Network*: turn on **LAN Only** and **Developer Mode** (this turns off the
Bambu Handy phone app). Put the printer's IP address, serial number and access code in
`config/settings.local.yaml` under `bambu_lan:` (see `settings.local.example.yaml`). The access code is a
password, so it never goes in `settings.yaml` (the app refuses to start if it does). Step 4 then shows
**Send to <printer>**: the file is uploaded (FTPS) and the print started (MQTT). The computer must be on the
same network as the printer, so this doesn't work from GitHub Codespaces.

## Sharing a test link

To let others try the app, set a password first, or anyone with the link can use it (and your AI key):
add `app_password: "..."` to `config/settings.local.yaml` (or set the `APP_PASSWORD` environment
variable), then restart. Everyone gets a password screen first.

**From a Codespace:** start the app, open the **Ports** tab, right-click port **8501** → **Port
Visibility** → **Public**, and copy the link from the **Forwarded Address** column. It works while the
Codespace is running. Testers can make and download print files, but USB saving and Wi-Fi sending only
work on a computer next to the printers.

## Rules that keep it reliable

- The AI only **chooses a part and fills in numbers**. Every value is checked against `min`/`max` in `validate.py` before anything is built.
- `python -m pytest` slices **every** part in the library. GitHub runs the tests on every push, so don't merge changes that fail them.
- Every job is saved in `jobs/<time>_<part>/`: the request, the final settings, the STL, the print file (G-code or .3mf) and the slicer log.
- Versions are pinned in `requirements.txt`. To upgrade, change the version on purpose, then run the tests.
- Parts whose license doesn't allow commercial use are blocked from printing. Each G-code file starts with a credit line for the part's designer.

## Status

- Working: library sync from the sheet, browsing and search, AI agent (Gemini free tier, Claude, or Ollama), size checks, OpenSCAD and STL parts, PrusaSlicer, preview, print time and cost.
- Working: one step per screen with the steps listed in the sidebar, a "How to use" guide, saving straight to an SD card / USB stick, and **Past prints** (reprint, change and print again, customer name/phone/notes and order status). Past prints live in `jobs/` on each computer.
- Working: several printers, sliced by OrcaSlicer (tested in CI with the real OrcaSlicer for the Ender 3 and the P2S). Sending to Bambu printers over Wi-Fi is tested against a fake printer, **not yet on a real P2S**.
- Not yet: OrcaSlicer backend (written but **untested**). Sending files over Wi-Fi is written for Klipper/Moonraker printers but **not yet tried on a real printer**; the button stays off until a printer is added in settings.
