from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import os
from pathlib import Path
from secrets import token_urlsafe
import socket
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import urlopen

from . import _
from .catalog import JoynError, PROFILE, RUNTIME

CDM_URL = ("https://archive.raspberrypi.com/debian/pool/main/w/widevine/"
           "libwidevinecdm0_4.10.2662.3+1_armhf.deb")
CDM_SHA256 = "17c6d1f144c5063a34fbd55e772b0dec8629fcb1869156ff2f03c22adb96d22f"
CDM_SIZE = 7465664
CDM = "libwidevinecdm.so"
CDM_LOCK = Lock()
LOOPBACK = "127.0.0.1"
START_TIMEOUT = 22


def widevine_payload(package):
    """Locate data.tar.xz in the verified Debian ar archive."""
    if not package.startswith(b"!<arch>\n"):
        raise ValueError("Invalid Widevine package")

    offset = 8
    while offset + 60 <= len(package):
        header = package[offset:offset + 60]
        size = int(header[48:58])
        offset += 60
        if header[:16].strip().rstrip(b"/") == b"data.tar.xz":
            return BytesIO(package[offset:offset + size])
        # ar members start on even byte boundaries.
        offset += size + size % 2

    raise ValueError("Missing Widevine package data")


def ensure_widevine():
    """Install a verified CDM in the writable profile on first use."""
    cache = PROFILE / "cdm-4.10.2662.3"
    target = cache / CDM
    with CDM_LOCK:
        if target.is_file():
            return target
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with urlopen(CDM_URL, timeout=15) as response:
                package = response.read(CDM_SIZE + 1)
            if len(package) != CDM_SIZE or sha256(package).hexdigest() != CDM_SHA256:
                raise ValueError("Widevine package checksum")
            with tarfile.open(fileobj=widevine_payload(package), mode="r:xz") as archive:
                library = next(m for m in archive if m.isfile() and Path(m.name).name == CDM)
                with TemporaryDirectory(dir=cache) as temporary:
                    staged = Path(temporary) / CDM
                    staged.write_bytes(archive.extractfile(library).read())
                    staged.replace(target)
        except (OSError, ValueError, StopIteration, tarfile.TarError) as error:
            raise JoynError(_("Widevine setup failed. Check the internet connection and retry.")) from error
    return target


class RelayHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_stream_headers(self):
        relay = self.server.relay
        if self.path != relay.path:
            self.send_error(404)
            return False
        # dash2ts produces one sequential stream. HEAD probes do not claim it.
        if self.command == "GET":
            with relay.resources:
                available = relay.client is None and not relay.closed.is_set()
                if available:
                    relay.client = self.connection
            if not available:
                self.send_error(409)
                return False

        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Connection", "close")
        self.end_headers()
        return True

    def do_HEAD(self):
        self.send_stream_headers()

    def do_GET(self):
        relay = self.server.relay
        try:
            if not self.send_stream_headers():
                return
            # The decoder may stop reading while paused or while its buffer drains.
            # Transport.close() interrupts a blocked write when the service stops.
            self.wfile.write(relay.initial)
            while not relay.closed.is_set():
                try:
                    block = relay.upstream.recv(65536)
                except TimeoutError:
                    continue
                if not block:
                    break
                self.wfile.write(block)
        except OSError:
            pass


class Transport:
    """Own one dash2ts child and its single-client loopback HTTP stream."""

    def __init__(self, stream, position=0, *, on_demand=False):
        self.stream = stream
        self.on_demand = on_demand
        self.position = position
        self.closed = Event()
        self.client = None
        self.resources = Lock()
        self.path = "/" + token_urlsafe(18) + ".ts"
        self.temp = self.child = self.server = self.upstream = None
        self.initial = b""

    def open(self):
        url = self.stream.get("url", "")
        if urlsplit(url).scheme not in ("http", "https"):
            raise JoynError(_("Joyn did not return a playable stream."))

        properties = self.stream.get("properties", {})
        license_key = properties.get("inputstream.adaptive.license_key")
        drm = self.stream.get("drm")
        if not license_key and not drm and not self.stream.get("license_url"):
            return url
        if not license_key or drm != "com.widevine.alpha":
            raise JoynError(_("Joyn returned an unsupported DRM configuration."))

        binary = RUNTIME / "dash2ts"
        adaptive = RUNTIME / "inputstream.adaptive"
        if not os.access(binary, os.X_OK) or not adaptive.is_dir():
            raise JoynError(_("The ARM playback runtime is missing. Reinstall the Joyn package."))
        library = ensure_widevine()
        try:
            with self.resources:
                self.check_open()
                self.temp = TemporaryDirectory(prefix="enigma2-joyn-")
                kodi = Path(self.temp.name)
                (kodi / "addons").mkdir()
                (kodi / "addons/inputstream.adaptive").symlink_to(adaptive, target_is_directory=True)
                (kodi / "cdm").mkdir()
                (kodi / "cdm" / CDM).symlink_to(library)

            with socket.socket() as listener:
                listener.bind((LOOPBACK, 0))
                listener.listen(1)
                listener.settimeout(0.5)
                command = [str(binary), "-u", url, "-k", str(kodi), "-p", str(listener.getsockname()[1]),
                           "-w", license_key]
                headers = properties.get("inputstream.adaptive.stream_headers")
                if headers:
                    command.extend(["-h", headers])
                if self.on_demand:
                    command.append("-f")
                if self.position:
                    command.extend(["-s", str(self.position)])
                with self.resources:
                    self.check_open()
                    # The ARM CDM resolves its EABI division routines through libgcc.
                    env = {**os.environ, "LD_LIBRARY_PATH": str(RUNTIME / "lib"),
                           "LD_PRELOAD": "/lib/libgcc_s.so.1"}
                    self.child = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                                                  stderr=subprocess.DEVNULL)
                self.await_data(listener)

            with self.resources:
                self.check_open()
                self.server = ThreadingHTTPServer((LOOPBACK, 0), RelayHandler)
                self.server.relay = self
                Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
                return f"http://{LOOPBACK}:{self.server.server_port}{self.path}"
        except OSError as error:
            self.close()
            raise JoynError(_("Joyn did not deliver video. The Widevine license may have been rejected.")) from error

    def check_open(self):
        if self.closed.is_set():
            raise OSError("Closed")

    def await_data(self, listener):
        """Keep the first bytes for the HTTP client and verify three TS sync bytes."""
        deadline = monotonic() + START_TIMEOUT
        while monotonic() < deadline:
            self.check_open()
            if self.child.poll() is not None:
                break
            try:
                if self.upstream is None:
                    upstream, _ = listener.accept()
                    with self.resources:
                        if self.closed.is_set():
                            upstream.close()
                            self.check_open()
                        upstream.settimeout(0.5)
                        self.upstream = upstream
                block = self.upstream.recv(65536)
            except TimeoutError:
                continue
            if not block:
                break
            self.initial += block
            if len(self.initial) >= 377:
                if self.initial[0] == self.initial[188] == self.initial[376] == 0x47:
                    return
                break
        raise OSError("MPEG-TS did not start")

    def close(self):
        with self.resources:
            if self.closed.is_set():
                return
            self.closed.set()
            for connection in (self.client, self.upstream):
                if connection is None:
                    continue
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            if self.upstream:
                self.upstream.close()
            server, child, temp = self.server, self.child, self.temp
        if server:
            server.shutdown()
            server.server_close()
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if temp:
            temp.cleanup()
