"""Assemble the resolver and ARM playback runtime from pinned upstream inputs."""
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
from hashlib import file_digest
from io import BytesIO
import json
import os
from pathlib import Path
from shutil import copy2, copyfileobj, copytree, rmtree, which
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
FEED = "https://feeds2.mynonpublic.com/8.0.1/"
TARGET_FEED = "vusolo4k/cortexa15hf-neon-vfpv4/"


def fetch(directory, name, url, digest):
    if not url.startswith("https://"):
        url = FEED + ("" if url.startswith("STATIC/") else TARGET_FEED) + url
    path = directory / name
    if not path.exists() or checksum(path) != digest:
        with TemporaryDirectory(dir=directory) as temporary:
            downloaded = Path(temporary) / name
            with urlopen(url, timeout=45) as response, downloaded.open("wb") as output:
                copyfileobj(response, output)
            if checksum(downloaded) != digest:
                raise ValueError(f"Checksum mismatch for {name}. Check {HERE / 'runtime-lock.json'}.")
            downloaded.replace(path)
    return path


def checksum(path):
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def unpack_ipk(package, root):
    with package.open("rb") as source:
        is_ar = source.read(8) == b"!<arch>\n"
    if is_ar:
        entries = subprocess.check_output(["ar", "t", package], text=True).splitlines()
        name = next(entry for entry in entries if entry.startswith("data.tar"))
        payload = subprocess.check_output(["ar", "p", package, name])
    else:
        with tarfile.open(package) as outer:
            entry = next(member for member in outer if Path(member.name).name.startswith("data.tar"))
            payload = outer.extractfile(entry).read()
    if payload.startswith(bytes.fromhex("28b52ffd")):
        payload = subprocess.check_output(["zstd", "-dc"], input=payload)
    with tarfile.open(fileobj=BytesIO(payload)) as archive:
        archive.extractall(root, filter="data")


def build_helper(source, sysroot, build):
    for patch in sorted(HERE.glob("*.patch")):
        subprocess.run(["patch", "-p1", "-i", patch], cwd=source, check=True)
    sh = HERE / "cross-build.sh"
    args = [source, sysroot]
    if os.getenv("JOYN_CROSS_ROOT") or which("arm-linux-gnueabihf-g++"):
        subprocess.run(["bash", sh, *args], check=True)
    else:
        docker = which("docker")
        if not docker:
            raise RuntimeError("Use an ARM hard-float cross compiler or install Docker.")
        subprocess.run(
            [docker, "run", "--rm", "-v", f"{build}:/work", "-v", f"{sh}:/cross-build.sh:ro",
             "ubuntu:26.04", "bash", "-euc",
             'apt-get -qq update; apt-get -qq install -y --no-install-recommends make g++-arm-linux-gnueabihf; '
             'uid=$1; gid=$2; shift 2; '
             'setpriv --reuid="$uid" --regid="$gid" --clear-groups bash /cross-build.sh "$@"',
             "build", str(os.getuid()), str(os.getgid()), *("/work/" + str(p.relative_to(build)) for p in args)],
            check=True,
        )
    return source / "build/dash2ts"


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-only", action="store_true", help="Fetch only the data needed by the browser in Docker.")
    options = parser.parse_args()
    downloads = PLUGIN / ".build/downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    runtime = PLUGIN / ".runtime"
    runtime.mkdir(exist_ok=True)
    entries = json.loads((HERE / "runtime-lock.json").read_text())
    if options.catalog_only:
        entries = {"joyn.zip": entries["joyn.zip"]}

    with ThreadPoolExecutor(max_workers=5) as executor:
        inputs = dict(zip(entries, executor.map(lambda entry: fetch(downloads, entry[0], *entry[1]), entries.items())))

    with TemporaryDirectory(dir=downloads.parent) as temporary:
        build = Path(temporary)
        addon = build / "addons"
        with ZipFile(inputs["joyn.zip"]) as archive:
            if any(Path(name).is_absolute() or ".." in Path(name).parts
                   or not Path(name).parts or Path(name).parts[0] != "plugin.video.joyn"
                   for name in archive.namelist()):
                raise ValueError("Invalid Joyn package")
            archive.extractall(addon)
        replace_dir(addon, runtime / "addons")
        if options.catalog_only:
            return
        sysroot = build / "sysroot"
        sysroot.mkdir()
        for name, archive in inputs.items():
            if name.endswith(".ipk"):
                unpack_ipk(archive, sysroot)
        with tarfile.open(inputs["dash2ts.tar.gz"]) as archive:
            archive.extractall(build, filter="data")
        binary = build_helper(next(build.glob("dash2ts-*")), sysroot, build)
        binary.replace(runtime / "dash2ts")
        libraries = build / "lib"
        libraries.mkdir()
        for name in ("libpugixml.so.1", "libtinyxml2.so.11"):
            copy2(sysroot / "usr/lib" / name, libraries / name)
        replace_dir(libraries, runtime / "lib")
        adaptive = sysroot / "usr/share/kodi/addons/inputstream.adaptive"
        # A real file keeps the package independent of the Kodi filesystem.
        copytree(adaptive, build / "inputstream.adaptive", ignore=lambda path, names: {
            name for name in names if (Path(path) / name).is_symlink()
        })
        replace_dir(build / "inputstream.adaptive", runtime / "inputstream.adaptive")


def replace_dir(source, target):
    if target.exists():
        rmtree(target)
    source.rename(target)


if __name__ == "__main__":
    main()
