"""Collect PR files from the run's input/PR folder and unpack zips safely."""
import calendar
import datetime
import hashlib
import io
import lzma
import os
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from pr_intake import limits
from pr_intake.filetype import sniff

_JUNK_NAMES = {".ds_store", "thumbs.db"}
_EXT_KIND = {".xlsx": "xlsx", ".pdf": "pdf", ".txt": "text", ".csv": "text", ".zip": "zip"}
_CHUNK = 64 * 1024
_ZIP_ERRORS = (zipfile.BadZipFile, zlib.error, lzma.LZMAError, EOFError, NotImplementedError, ValueError, OSError)


@dataclass
class FileEntry:
    id: str
    source: str
    local_path: str
    kind: str
    size: int
    mtime: float
    sha256: str


class Rejection(Exception):
    def __init__(self, code: str, file: str, why: str):
        super().__init__(f"{code}: {file}: {why}")
        self.code = code
        self.file = file
        self.why = why


def _safe(rel: str) -> bool:
    # Same rules as blob._safe.
    parts = rel.split("/")
    return all(p not in ("", ".", "..") and "\\" not in p and ":" not in p
               and not any(ord(ch) < 32 for ch in p) for p in parts)


def child_dir(parent: Path, name: str) -> Path | None:
    """The first sub-folder (by name) of parent whose lower-cased name is name; None if none."""
    if not parent.is_dir():
        return None
    for c in sorted(parent.iterdir(), key=lambda p: p.name):
        if c.is_dir() and c.name.lower() == name:
            return c
    return None


def find_pr_dir(run_dir: Path) -> Path | None:
    inp = child_dir(Path(run_dir), "input")
    return child_dir(inp, "pr") if inp else None


def _is_junk(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    base = parts[-1].lower()
    return ("__macosx" in (p.lower() for p in parts)
            or base in _JUNK_NAMES or base.startswith("._"))


def _too_big(file: str) -> Rejection:
    return Rejection("TOO_LARGE", file, f"more than {limits.MAX_TOTAL_BYTES // (1024 * 1024)} MB")


def _too_many(file: str) -> Rejection:
    return Rejection("TOO_LARGE", file, f"more than {limits.MAX_FILES} files")


class _Collector:
    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)
        self.entries: list[FileEntry] = []
        self.notes: list[str] = []
        self.by_hash: dict[str, str] = {}
        self.total = 0
        self.zip_n = 0

    def _count(self, n: int, file: str) -> None:
        self.total += n
        if self.total > limits.MAX_TOTAL_BYTES:
            raise _too_big(file)

    def add(self, source: str, data: bytes, mtime: float, path: Path | None, write_to: Path | None):
        kind = sniff(data)
        if kind == "unsupported":
            raise Rejection("UNSUPPORTED_FILE", source, "unsupported type")
        name = source.rsplit(" > ", 1)[-1]
        ext = os.path.splitext(name)[1].lower()
        if ext in _EXT_KIND and _EXT_KIND[ext] != kind:
            self.notes.append(f"extension {ext} but content is {kind}: {source}")
        digest = hashlib.sha256(data).hexdigest()
        if digest in self.by_hash:
            self.notes.append(f"duplicate of {self.by_hash[digest]}: {source}")
            return
        if len(self.entries) >= limits.MAX_FILES:
            raise _too_many(source)
        eid = f"{len(self.entries) + 1:03d}"
        if write_to is not None:
            write_to.parent.mkdir(parents=True, exist_ok=True)
            write_to.write_bytes(data)
            path = write_to
        self.by_hash[digest] = eid
        self.entries.append(FileEntry(eid, source, str(path), kind, len(data), mtime, digest))

    def loose(self, path: Path, display: str) -> None:
        if _is_junk(display):
            self.notes.append(f"skipped junk entry: {display}")
            return
        st = path.stat()
        if self.total + st.st_size > limits.MAX_TOTAL_BYTES:
            raise _too_big(display)
        data = path.read_bytes()
        self._count(len(data), display)
        kind = sniff(data)
        if display.lower().endswith(".zip"):
            if kind in ("zip", "xlsx"):
                # A real zip container (an xlsx renamed .zip is still opened as a zip).
                self.unzip(data, display, st.st_mtime)
            elif kind == "unsupported":
                raise Rejection("ZIP_REJECTED", display, "cannot be opened")
            else:
                self.add(display, data, st.st_mtime, path, None)
        elif kind == "zip":
            raise Rejection("UNSUPPORTED_FILE", display, "unsupported type")
        else:
            self.add(display, data, st.st_mtime, path, None)

    def unzip(self, data: bytes, display: str, fallback_mtime: float) -> None:
        self.zip_n += 1
        zid = f"z{self.zip_n}"
        self.total -= len(data)  # the zip itself is not unpacked content

        def rej(why: str) -> Rejection:
            return Rejection("ZIP_REJECTED", display, why)

        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except _ZIP_ERRORS:
            raise rej("cannot be opened") from None
        with zf:
            all_names = [i.filename.rstrip("/").casefold() for i in zf.infolist()]
            seen = set()
            kept = []
            for zi in zf.infolist():
                if zi.filename.endswith("/"):
                    continue
                name = zi.filename
                # orig_filename: zipfile rewrites a backslash to "/" on Windows; check the raw name.
                if (not _safe(name) or not _safe(zi.orig_filename) or ((zi.external_attr >> 16) & 0o170000) == 0o120000
                        or name.casefold() in seen):
                    raise rej("unsafe paths")
                seen.add(name.casefold())
                folded = name.casefold() + "/"
                if any(o.startswith(folded) for o in all_names):  # file/folder clash
                    raise rej("unsafe paths")
                if zi.flag_bits & 0x1:
                    raise rej("password protected")
                if _is_junk(name):
                    self.notes.append(f"skipped junk entry: {display} > {name}")
                    continue
                if name.lower().endswith(".zip"):
                    raise rej("contains another zip")
                kept.append(zi)
            if len(kept) > limits.MAX_FILES:
                raise _too_many(display)
            if self.total + sum(z.file_size for z in kept) > limits.MAX_TOTAL_BYTES:
                raise _too_big(display)
            # Ratio is per zip (sum of sizes), not per entry.
            if (sum(z.file_size for z in kept)
                    > limits.MAX_ZIP_RATIO * max(sum(z.compress_size for z in kept), 1)):
                raise rej("too large when unpacked")

            blobs = []
            try:
                for zi in kept:
                    buf = bytearray()
                    # zipfile never yields more than the declared file_size and raises
                    # BadZipFile on a CRC mismatch, so a lying directory cannot overrun.
                    with zf.open(zi) as f:
                        while True:
                            chunk = f.read(_CHUNK)
                            if not chunk:
                                break
                            buf += chunk
                            self._count(len(chunk), display)
                    blobs.append((zi, bytes(buf)))
            except _ZIP_ERRORS:
                raise rej("cannot be opened") from None

        for _, buf in blobs:
            if sniff(buf) == "zip":
                raise rej("contains another zip")
        for zi, buf in blobs:
            try:
                datetime.datetime(*zi.date_time)
                mtime = float(calendar.timegm(tuple(zi.date_time) + (0, 0, 0)))
            except (ValueError, OverflowError):
                mtime = fallback_mtime
            dest = self.out_dir / "unzipped" / zid / Path(*zi.filename.split("/"))
            self.add(f"{display} > {zi.filename}", buf, mtime, None, dest)


def collect(pr_dir: Path, out_dir: Path) -> tuple[list[FileEntry], list[str]]:
    pr_dir = Path(pr_dir)
    rels = sorted((p.relative_to(pr_dir).as_posix() for p in pr_dir.rglob("*") if p.is_file()),
                  key=lambda r: (r.lower(), r))
    c = _Collector(out_dir)
    for rel in rels:
        c.loose(pr_dir.joinpath(*rel.split("/")), rel)
    return c.entries, c.notes
