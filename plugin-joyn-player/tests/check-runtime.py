from collections import Counter
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from shutil import copyfileobj
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread


def capture(runtime, directory, url, start):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(20)
        helper = subprocess.Popen(
            [runtime / "dash2ts", "-u", url, "-k", directory, "-f", "-s", str(start),
             "-p", str(listener.getsockname()[1])],
            env={**os.environ, "LD_LIBRARY_PATH": str(runtime / "lib"), "LD_PRELOAD": "/lib/libgcc_s.so.1"},
            stdout=subprocess.DEVNULL,
        )
        try:
            client, _ = listener.accept()
            with client:
                client.settimeout(20)
                with client.makefile("rb") as source, (directory / "output.ts").open("wb") as target:
                    copyfileobj(source, target)
        finally:
            if helper.poll() is None:
                helper.terminate()
                try:
                    helper.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    helper.kill()
                    helper.wait()
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v", "-show_packets", "-count_frames",
        "-show_entries", "stream=width,height,nb_read_frames:packet=pts,dts", "-of", "json",
        directory / "output.ts",
    ]))


def check(runtime, directory, manifest):
    (directory / "addons").mkdir()
    (directory / "cdm").mkdir()
    (directory / "addons/inputstream.adaptive").symlink_to(runtime / "inputstream.adaptive")
    with ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=manifest.parent)) as server:
        Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/{manifest.name}"
            full = capture(runtime, directory, url, 0)
            seek = capture(runtime, directory, url, 6)
        finally:
            server.shutdown()

    for sample in (full, seek):
        stream, = sample["streams"]
        if (stream["width"], stream["height"]) != (320, 180):
            raise ValueError(f"Wrong video track: {stream}")
        packets = sample["packets"]
        if int(stream["nb_read_frames"]) != len(packets):
            raise ValueError(f"Only {stream['nb_read_frames']} of {len(packets)} pictures decoded")
        if any(p["pts"] < p["dts"] for p in packets):
            raise ValueError("Picture due before decode")
        pts = sorted(p["pts"] for p in packets)
        steps = Counter(b - a for a, b in zip(pts, pts[1:]))
        if set(steps) != {90000 // 24}:
            raise ValueError(f"Unexpected presentation intervals: {steps}")
    if len(full["packets"]) != 8 * 24:
        raise ValueError(f"Expected 192 pictures, got {len(full['packets'])}")
    if len(seek["packets"]) >= len(full["packets"]):
        raise ValueError("Seek restarted the film from the beginning")
    if max(p["pts"] for p in seek["packets"]) != max(p["pts"] for p in full["packets"]):
        raise ValueError("Seek did not reach the same end of stream")
    print("PASS: higher-quality track, all 192 pictures, 24 fps presentation cadence and seek to end")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: check-runtime.py RUNTIME_DIRECTORY TEST_MANIFEST")
    runtime, manifest = (Path(arg).resolve() for arg in sys.argv[1:])
    with TemporaryDirectory() as temporary:
        check(runtime, Path(temporary), manifest)
