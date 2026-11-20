"""A tiny stand-in for a Bambu printer's file server: implicit FTPS on a local port,
user bblp + access code, passive mode, STOR into a folder. Only what the app uses."""
import socket
import ssl
import subprocess
import threading
from pathlib import Path


def self_signed(folder: Path) -> tuple[Path, Path]:
    key, cert = folder / "key.pem", folder / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(cert), "-days", "1", "-subj", "/CN=bambu-test"],
                   check=True, capture_output=True)
    return cert, key


class FakePrinterFTPS:
    def __init__(self, folder: Path, code: str, cert: Path, key: str):
        self.folder, self.code = folder, code
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, key)
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        self.reused = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                raw, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(raw,), daemon=True).start()

    def _session(self, raw):
        c = self.ctx.wrap_socket(raw, server_side=True)
        f = c.makefile("rwb")
        say = lambda s: (f.write((s + "\r\n").encode()), f.flush())
        say("220 Bambu fake ready")
        user, ok, pasv = None, False, None
        for line in f:
            cmd, _, arg = line.decode().strip().partition(" ")
            cmd = cmd.upper()
            if cmd == "USER":
                user = arg; say("331 password please")
            elif cmd == "PASS":
                ok = user == "bblp" and arg == self.code
                say("230 logged in" if ok else "530 Login incorrect.")
            elif not ok:
                say("530 Please login.")
            elif cmd in ("PBSZ", "PROT", "TYPE"):
                say("200 ok")
            elif cmd == "PASV":
                pasv = socket.create_server(("127.0.0.1", 0))
                p = pasv.getsockname()[1]
                say(f"227 Entering Passive Mode (127,0,0,1,{p // 256},{p % 256})")
            elif cmd == "STOR":
                say("150 send it")
                d, _ = pasv.accept()
                d = self.ctx.wrap_socket(d, server_side=True)
                data = b""
                try:
                    while chunk := d.recv(65536):
                        data += chunk
                except (ssl.SSLError, ConnectionResetError):
                    pass            # client closed without a TLS goodbye: fine, like the printer
                self.reused.append(d.session_reused)
                d.close(); pasv.close()
                (self.folder / arg).write_bytes(data)
                say("226 done")
            elif cmd == "QUIT":
                say("221 bye"); break
            else:
                say("502 not here")
        c.close()

    def close(self):
        self.srv.close()
