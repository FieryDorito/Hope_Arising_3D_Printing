"""Send a sliced .gcode.3mf to a Bambu Lab printer over Wi-Fi, and start it.

How Bambu printers take a file on the local network (LAN Only + Developer Mode on the printer):
  1. upload the file with FTPS (port 990, user "bblp", password = the printer's access code)
  2. tell the printer to print it with an MQTT message (port 8883, same login)

Printers are set up in config/settings.local.yaml (the access code is a password, so it is
never put in settings.yaml, which goes on GitHub):

    bambu_lan:
      Bambu Lab P2S:              # the printer's name in settings.yaml
        address: 192.168.1.60
        serial: "01P00A123456789"
        access_code: "12345678"
        use_ams: false            # true if the filament comes from an AMS
"""
from __future__ import annotations

import ftplib
import json
import socket
import ssl
import time
from dataclasses import dataclass
from pathlib import Path

from .config import settings

FTP_PORT = 990
MQTT_PORT = 8883
USER = "bblp"


class BambuSendError(Exception):
    """Sending failed. Message is shown to the operator."""


@dataclass
class BambuLan:
    name: str
    address: str
    serial: str
    access_code: str
    use_ams: bool = False
    bed_leveling: bool = True
    timelapse: bool = False


def lan_config(printer_name: str) -> BambuLan | None:
    """The Wi-Fi details for this printer from settings.local.yaml, or None if not set up."""
    c = (settings().get("bambu_lan") or {}).get(printer_name)
    if not isinstance(c, dict):
        return None
    address, serial, code = (str(c.get(k) or "").strip() for k in ("address", "serial", "access_code"))
    if not (address and serial and code):
        return None
    return BambuLan(printer_name, address, serial, code, bool(c.get("use_ams", False)),
                    bool(c.get("bed_leveling", True)), bool(c.get("timelapse", False)))


def _tls() -> ssl.SSLContext:
    # The printer uses its own self-made certificate, so it can't be checked against a public
    # authority. The access code is what keeps strangers out, and this only runs on the LAN.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class _ImplicitFTPS(ftplib.FTP_TLS):
    """FTPS where the connection is encrypted from the first byte (port 990), reusing the TLS
    session for file transfers - Bambu printers refuse transfers otherwise."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value, server_hostname=self.host)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size

    def upload(self, name: str, fh) -> None:
        self.voidcmd("TYPE I")
        conn = self.transfercmd(f"STOR {name}")
        try:
            while chunk := fh.read(64 * 1024):
                conn.sendall(chunk)
            # Say "that's all" without a TLS goodbye (Bambu printers hang on that), then wait
            # for the printer to confirm BEFORE closing, so no data is lost on the way.
            try:
                conn.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            self.voidresp()
        finally:
            conn.close()


def upload(lan: BambuLan, file: Path, timeout: float = 60) -> None:
    ftp = _ImplicitFTPS(context=_tls(), timeout=timeout)
    try:
        ftp.connect(lan.address, FTP_PORT)
        ftp.login(USER, lan.access_code)
        ftp.prot_p()
        with open(file, "rb") as fh:
            ftp.upload(file.name, fh)
    except ftplib.error_perm as e:
        if str(e).startswith("530"):
            raise BambuSendError(f"{lan.name} said the access code is wrong. Check the code on the printer "
                                 "screen (it changes when LAN Only mode is switched off and on).") from e
        raise BambuSendError(f"{lan.name} refused the file ({e}). Is the printer's storage full?") from e
    except (socket.timeout, TimeoutError) as e:
        raise BambuSendError(f"{lan.name} did not answer in time. Check it is on and on the same Wi-Fi.") from e
    except (ConnectionRefusedError, OSError) as e:
        raise BambuSendError(f"Could not reach {lan.name} at {lan.address}. Check it is switched on, on the "
                             "same Wi-Fi as this computer, and that LAN Only mode and Developer Mode are "
                             "on (printer screen: Settings > Network).") from e
    finally:
        try:
            ftp.close()
        except Exception:
            pass


def print_command(lan: BambuLan, file_name: str, plate: int = 1) -> dict:
    """The MQTT message that makes the printer print a file already uploaded to its storage."""
    return {"print": {
        "sequence_id": str(int(time.time()) % 100000),
        "command": "project_file",
        "param": f"Metadata/plate_{plate}.gcode",
        "url": f"ftp:///{file_name}",
        "subtask_name": file_name.removesuffix(".gcode.3mf"),
        "project_id": "0", "profile_id": "0", "task_id": "0", "subtask_id": "0",
        "md5": "",
        "timelapse": lan.timelapse,
        "bed_leveling": lan.bed_leveling,
        "flow_cali": False,
        "vibration_cali": True,
        "layer_inspect": False,
        "use_ams": lan.use_ams,
        "ams_mapping": [0] if lan.use_ams else [],
    }}


def start_print(lan: BambuLan, file_name: str, timeout: float = 20) -> None:
    try:
        import paho.mqtt.client as mqtt
    except ImportError as e:
        raise BambuSendError("The Wi-Fi printing part is not installed. Run start.bat / start.sh again.") from e

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"hope-arising-{int(time.time())}")
    client.username_pw_set(USER, lan.access_code)
    client.tls_set_context(_tls())
    client.tls_insecure_set(True)
    result: dict = {}
    client.on_connect = lambda c, u, f, rc, p=None: result.setdefault("rc", rc)
    try:
        client.connect(lan.address, MQTT_PORT, keepalive=30)
    except (OSError, socket.timeout) as e:
        raise BambuSendError(f"The file is on {lan.name}, but the app could not tell it to start. Start it from "
                             "the printer screen, and check Developer Mode is on.") from e
    client.loop_start()
    try:
        end = time.time() + timeout
        while "rc" not in result and time.time() < end:
            time.sleep(0.1)
        rc = result.get("rc")
        if rc is None:
            raise BambuSendError(f"{lan.name} did not answer. The file is on the printer; start it from its screen.")
        if getattr(rc, "is_failure", rc != 0):
            raise BambuSendError(f"{lan.name} would not take commands ({rc}). Check the access code, and that "
                                 "Developer Mode is on. The file is on the printer; start it from its screen.")
        info = client.publish(f"device/{lan.serial}/request", json.dumps(print_command(lan, file_name)), qos=1)
        info.wait_for_publish(timeout)
        if not info.is_published():
            raise BambuSendError(f"{lan.name} did not confirm. Check the printer screen.")
    finally:
        client.loop_stop()
        client.disconnect()


def send(lan: BambuLan, files: list[Path], start: bool = False) -> None:
    """Upload every file; start=True also starts the first one."""
    for f in files:
        upload(lan, f)
    if start and files:
        start_print(lan, files[0].name)
