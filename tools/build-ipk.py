"""Build an opkg package from a control file and an explicit set of plugin assets."""

from argparse import ArgumentParser
from gzip import GzipFile
from io import BytesIO
from pathlib import Path, PurePosixPath
from re import fullmatch
import tarfile
from tempfile import TemporaryDirectory


EXTENSIONS = PurePosixPath("usr/lib/enigma2/python/Plugins/Extensions")


def tar_gz(files):
    output = BytesIO()
    directories = sorted({parent for path in files for parent in path.parents if parent.parts})
    with GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
            for directory in directories:
                entry = tarfile.TarInfo(f"./{directory}")
                entry.type = tarfile.DIRTYPE
                entry.mode = 0o755
                archive.addfile(entry)
            for path, content in files.items():
                entry = tarfile.TarInfo(f"./{path}")
                entry.size = len(content)
                entry.mode = 0o644
                archive.addfile(entry, BytesIO(content))
    return output.getvalue()


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("control", type=Path)
    parser.add_argument("extension", help="Enigma2 extension directory name")
    parser.add_argument("output", type=Path)
    parser.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args()

    if not fullmatch(r"[A-Za-z][A-Za-z0-9_]*", args.extension):
        parser.error("the extension must be a Python package directory name")
    if any(path.is_absolute() or ".." in path.parts for path in args.files):
        parser.error("package files must use paths relative to the plugin directory")

    control = args.control.read_bytes()
    fields = dict(line.split(": ", 1) for line in control.decode().splitlines() if ": " in line)
    name = fields.get("Package", "")
    version = fields.get("Version", "")
    arch = fields.get("Architecture", "")
    if not all(fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+~-]*", value) for value in (name, version, arch)):
        parser.error("control must contain valid Package, Version and Architecture fields")

    destination = EXTENSIONS / args.extension
    payload = {destination / path: path.read_bytes() for path in sorted(args.files)}
    package = tar_gz({
        PurePosixPath("debian-binary"): b"2.0\n",
        PurePosixPath("control.tar.gz"): tar_gz({PurePosixPath("control"): control}),
        PurePosixPath("data.tar.gz"): tar_gz(payload),
    })

    args.output.mkdir(parents=True, exist_ok=True)
    filename = f"{name}_{version}_{arch}.ipk"
    final_path = args.output / filename
    with TemporaryDirectory(dir=args.output) as temporary:
        package_path = Path(temporary) / filename
        package_path.write_bytes(package)
        package_path.replace(final_path)
    print(final_path)


if __name__ == "__main__":
    main()
