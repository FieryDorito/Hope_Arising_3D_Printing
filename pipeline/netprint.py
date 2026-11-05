"""Send a print file to a printer over Wi-Fi / network.  EXPERIMENTAL - not yet tried on a real printer.

Works with printers running Klipper + Moonraker (the usual web API for Klipper printers,
e.g. Mainsail or Fluidd). Ender 3 printers have no network, so they keep using the SD card.
Creality's own Wi-Fi printers (K1C, Ender 3 V3 KE) run Klipper, but on the stock firmware
Moonraker may need to be switched on first - check this before relying on it.

Printers are listed in settings.yaml:

    network_printers:
      - name: K1C by the window
        address: 192.168.1.50        # the printer's IP address (printer screen > network)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests

from .config import settings


class NetPrintError(Exception):
    """Sending failed. Message is shown to the operator."""


@dataclass
class NetPrinter:
    name: str
    address: str


def configured_printers() -> list[NetPrinter]:
    out = []
    for p in settings().get("network_printers") or []:
        if isinstance(p, dict) and p.get("address"):
            out.append(NetPrinter(str(p.get("name") or p["address"]), str(p["address"]).strip()))
    return out


def _base_url(address: str) -> str:
    a = address.rstrip("/")
    return a if a.startswith(("http://", "https://")) else f"http://{a}"


def send(printer: NetPrinter, gcode: Path, start: bool = False, timeout: float = 60) -> None:
    """Upload the file to the printer. start=True also starts printing it."""
    url = _base_url(printer.address) + "/server/files/upload"
    try:
        with open(gcode, "rb") as fh:
            r = requests.post(url, files={"file": (gcode.name, fh, "application/octet-stream")},
                              data={"root": "gcodes", "print": "true" if start else "false"},
                              timeout=timeout)
    except requests.ConnectionError as e:
        raise NetPrintError(f"Could not reach {printer.name}. Check it is switched on and on the "
                            "same Wi-Fi as this computer.") from e
    except requests.Timeout as e:
        raise NetPrintError(f"{printer.name} did not answer in time. Try again, or use the SD card.") from e
    if r.status_code >= 400:
        raise NetPrintError(f"{printer.name} refused the file (code {r.status_code}). Use the SD card "
                            "for now and tell the student team.")
