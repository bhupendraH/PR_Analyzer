"""PR intake step: inventory and preview the run's PR files.

Usage:
    python3 intake.py prepare <run_dir> <intake_dir>
        Collect input/PR files (unpacking zips), extract each to
        <intake_dir>/extracted/<id>.json and <intake_dir>/previews/<id>.txt,
        and write <intake_dir>/files.json. <intake_dir> must be new or empty.
        manifest.json is written only on REJECTED or NOTHING_TO_DO.

Prints exactly one line and exits with:
    0  PREPARED <n> file(s)
    1  USAGE ...
    3  ERROR <reason>
    6  REJECTED <CODE> <file>
    7  NOTHING_TO_DO no PR files
"""
import dataclasses
import json
import sys
from pathlib import Path

from pr_intake import extract, limits, manifest, messages, unpack

USAGE = "USAGE intake.py prepare <run_dir> <intake_dir>"


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _reject(intake_dir: Path, code: str, file: str, why: str, notes: list[str]) -> tuple[int, str]:
    name = messages.safe_name(file)
    msg = messages.result_message(code, file=name, why=why)
    m = manifest.Manifest(decision="REJECTED", outcome=code, result_message=msg,
                          detail=f"{code} {name} {why}".rstrip(), notes=list(notes), files=[], credit_used=0)
    manifest.write_manifest(intake_dir / "manifest.json", m)
    return 6, f"REJECTED {code} {name}".rstrip()


def prepare(run_dir: Path, intake_dir: Path, ocr) -> tuple[int, str]:
    if intake_dir.exists() and (not intake_dir.is_dir() or any(intake_dir.iterdir())):
        return 3, "ERROR intake dir is not empty"
    intake_dir.mkdir(parents=True, exist_ok=True)
    if ocr is not None and not ocr.available():
        ocr = None
    pr_dir = unpack.find_pr_dir(run_dir)
    notes: list[str] = []
    entries: list[unpack.FileEntry] = []
    if pr_dir is not None:
        try:
            entries, notes = unpack.collect(pr_dir, intake_dir)
        except unpack.Rejection as r:
            return _reject(intake_dir, r.code, r.file, r.why, notes)
    if not entries:
        m = manifest.Manifest(decision="NOTHING_TO_DO", outcome="NOTHING_TO_DO",
                              result_message=messages.result_message("NOTHING_TO_DO"),
                              detail="NOTHING_TO_DO no PR files", notes=notes, files=[], credit_used=0)
        manifest.write_manifest(intake_dir / "manifest.json", m)
        return 7, "NOTHING_TO_DO no PR files"
    (intake_dir / "extracted").mkdir()
    (intake_dir / "previews").mkdir()
    ocr_total = 0
    for e in entries:
        try:
            data = extract.extract(e, ocr)
        except extract.ExtractError:
            return _reject(intake_dir, "UNSUPPORTED_FILE", e.source, "unsupported type", notes)
        except extract.OcrUnavailable:
            return _reject(intake_dir, "OCR_UNAVAILABLE", e.source, "scan reader not available", notes)
        except extract.TooManyOcrPages:
            return _reject(intake_dir, "TOO_MANY_SCAN_PAGES", e.source, "too many scanned pages", notes)
        ocr_total += len(data.get("ocr_pages") or [])
        if ocr_total > limits.MAX_OCR_PAGES:
            return _reject(intake_dir, "TOO_MANY_SCAN_PAGES", e.source, "too many scanned pages", notes)
        _write_json(intake_dir / "extracted" / f"{e.id}.json", data)
        (intake_dir / "previews" / f"{e.id}.txt").write_text(
            extract.render_preview(data, e.source), encoding="utf-8")
    _write_json(intake_dir / "files.json", [dataclasses.asdict(e) for e in entries])
    return 0, f"PREPARED {len(entries)} file(s)"


def run(argv: list[str], ocr=None) -> tuple[int, str]:
    if len(argv) != 3 or argv[0] != "prepare":
        return 1, USAGE
    try:
        return prepare(Path(argv[1]), Path(argv[2]), ocr)
    except Exception as exc:  # never echo the message
        return 3, f"ERROR {type(exc).__name__}"


def main() -> None:
    ocr = None
    if sys.argv[1:2] == ["prepare"]:
        from pr_intake.ocr import TesseractEngine
        ocr = TesseractEngine()
    code, line = run(sys.argv[1:], ocr)
    print(line)
    sys.exit(code)


if __name__ == "__main__":
    main()
