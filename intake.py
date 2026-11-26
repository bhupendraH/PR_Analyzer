"""PR intake step: inventory and preview the run's PR files.

Usage:
    python3 intake.py prepare <run_dir> <intake_dir>
        Collect input/PR files (unpacking zips), extract each to
        <intake_dir>/extracted/<id>.json and <intake_dir>/previews/<id>.txt,
        and write <intake_dir>/files.json. <intake_dir> must be new or empty.
        manifest.json is written only on REJECTED or NOTHING_TO_DO; on success
        <intake_dir>/prepare_notes.json holds the unpack notes.

    python3 intake.py verify <run_dir> <intake_dir>
        Check each reviewer verdict (<intake_dir>/verdicts/<id>.json) against the
        extracted data, decide the run, write <intake_dir>/manifest.json and, only
        when ACCEPTED, the standard tables <intake_dir>/tables/<id>.csv
        (<id>-<n>.csv when a file has several tables). Refuses unless prepare
        succeeded (files.json present, no manifest.json yet).

Prints exactly one line and exits with:
    0  PREPARED <n> file(s) | ACCEPTED <n> file(s)
    1  USAGE ...
    3  ERROR <reason>
    6  REJECTED <CODE> <file> | REJECTED NOT_A_PR <n> of <m> file(s) | REJECTED REVIEW_FAILED <n> of <m> file(s)
       | REJECTED SCAN_UNREADABLE <file> page <p>
       (REVIEW_FAILED: every rejected file could not be checked, i.e. its verdict is missing or broken)
    7  NOTHING_TO_DO no PR files | NOTHING_TO_DO no PR files for <from>-<to>
"""
import csv
import dataclasses
import json
import re
import sys
import unicodedata
from pathlib import Path

from pr_intake import checks, extract, inject, limits, manifest, messages, months, recipe, unpack

USAGE = "USAGE intake.py prepare|verify <run_dir> <intake_dir>"
_SOURCE_COLS = ["source_file", "source_location", "source_kind", "copied_from"]
_OCR_CHECKS = {"ocr_confidence", "ocr_gstin", "ocr_totals"}


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
    _write_json(intake_dir / "prepare_notes.json", notes)
    return 0, f"PREPARED {len(entries)} file(s)"


# ---------- verify ----------

def _clean_reason(s: str) -> str:
    """Reviewer free text for the manifest: control characters removed, one line, at most 200 chars."""
    out = "".join(" " if ch.isspace() else ch for ch in s if ch.isspace() or unicodedata.category(ch)[0] != "C")
    return " ".join(out.split())[:200]


def _page(where: str, ocr_pages: list[int]) -> int:
    mt = re.fullmatch(r"page:(\d+)|p(\d+):L\d+", where or "")
    if mt:
        return int(mt.group(1) or mt.group(2))
    return ocr_pages[0] if ocr_pages else 1


class _FileState:
    """One file's verify result: its manifest entry, built tables and run-level classification."""

    def __init__(self, e: unpack.FileEntry, ex: dict):
        self.entry = e
        self.name = messages.safe_name(e.source)
        self.tables: list = []
        self.status = "UNCHECKED"  # ACCEPTED | NOT_PR | UNCHECKED | SCAN_UNREADABLE
        self.why = messages.CHECK_SENTENCES["unchecked"]
        self.page = 0
        self.dropped_where = 0
        self.result = manifest.FileResult(
            id=e.id, source=e.source, kind=e.kind, pages=ex.get("pages"), ocr_pages=list(ex.get("ocr_pages") or []),
            accepted=False, verdict="UNCHECKED", document_kind="", reason_code="unchecked", reviewer_reason="",
            checks=[], instruction_like=[])

    @property
    def rejected(self) -> bool:
        return self.status in ("NOT_PR", "UNCHECKED")


def _check_file(intake_dir: Path, e: unpack.FileEntry) -> _FileState:
    ex = json.loads((intake_dir / "extracted" / f"{e.id}.json").read_text(encoding="utf-8"))
    st = _FileState(e, ex)
    fr = st.result
    try:
        v = recipe.load_verdict(intake_dir / "verdicts" / f"{e.id}.json", e.id)
    except recipe.VerdictError:
        return st
    fr.document_kind = v["document_kind"]
    fr.reviewer_reason = _clean_reason(v["reason"])
    fr.instruction_like = ["reviewer: " + w["where"] for w in v["instruction_like_text"]]
    st.dropped_where = v["_dropped_where"]
    if not v["is_pr"]:
        st.status, fr.verdict, fr.reason_code = "NOT_PR", "NOT_PR", v["document_kind"]
        st.why = messages.KIND_LABELS[v["document_kind"]]
        return st
    try:
        st.tables = recipe.apply_verdict(ex, v, e.id, e.source, fr.ocr_pages)
    except recipe.VerdictError:  # the recipe does not fit this file: could not be checked
        return st
    fr.verdict = "PR"
    for t in st.tables:
        for k, val in t.entity.items():
            if val and not fr.entity.get(k):
                fr.entity[k] = val
    results = []
    for t in st.tables:
        for c in checks.run_checks(t):
            results.append(c)
            fr.checks.append(dict(dataclasses.asdict(c), sheet=t.sheet))
    failed = [c for c in results if not c.passed]
    ocr_failed = [c for c in failed if c.name in _OCR_CHECKS]
    if ocr_failed:
        st.status, fr.reason_code = "SCAN_UNREADABLE", ocr_failed[0].code
        st.page = _page(ocr_failed[0].where, fr.ocr_pages)
        st.why = messages.CHECK_SENTENCES[ocr_failed[0].code]
    elif failed:
        st.status, fr.verdict, fr.reason_code = "NOT_PR", "NOT_PR", failed[0].code
        st.why = messages.CHECK_SENTENCES[failed[0].code]
    else:
        st.status, fr.accepted, fr.reason_code = "ACCEPTED", True, ""
    return st


def _write_table(path: Path, st: _FileState, t) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(list(t.fields) + _SOURCE_COLS)
        for row, loc, cp, is_ocr in zip(t.rows, t.locations, t.copied, t.ocr_rows):
            w.writerow([row.get(fld, "") for fld in t.fields] +
                       [st.entry.source, loc, "ocr" if is_ocr else st.entry.kind, ",".join(cp)])


def _notes(prep_notes: list[str], states: list[_FileState], month_notes: list[str]) -> list[str]:
    notes = list(prep_notes)
    for st in states:
        for t in st.tables:
            notes.extend(f"{st.name}: {n}" for n in t.notes)
        if st.result.ocr_pages:
            pages = ", ".join(str(p) for p in st.result.ocr_pages)
            notes.append(f"{st.name}: scanned page(s) {pages} read by OCR")
    notes.extend(month_notes)
    # The reviewer's own reason stays in files[].reviewer_reason only: notes reach run_status.json in Blob.
    for st in states:
        if st.dropped_where:
            notes.append(f"{st.name}: {st.dropped_where} reviewer instruction-like location(s) dropped "
                         "(not in an allowed form)")
    return notes


def _add_detector_hits(states: list[_FileState]) -> None:
    for st in states:
        hits = [h for t in st.tables for h in inject.find_instruction_like(t)]
        st.result.instruction_like = hits + st.result.instruction_like


def _finish(intake_dir: Path, states, decision, outcome, msg, detail, notes) -> None:
    m = manifest.Manifest(decision=decision, outcome=outcome, result_message=msg, detail=detail, notes=notes,
                          files=[st.result for st in states])
    manifest.write_manifest(intake_dir / "manifest.json", m)


def verify(run_dir: Path, intake_dir: Path) -> tuple[int, str]:
    files_json = intake_dir / "files.json"
    if not files_json.is_file() or (intake_dir / "manifest.json").exists():
        return 3, "ERROR prepare has not run"
    entries = [unpack.FileEntry(**d) for d in json.loads(files_json.read_text(encoding="utf-8"))]
    pn = intake_dir / "prepare_notes.json"
    prep_notes = json.loads(pn.read_text(encoding="utf-8")) if pn.is_file() else []
    states = [_check_file(intake_dir, e) for e in entries]

    bad = [st for st in states if st.rejected]
    scan = [st for st in states if st.status == "SCAN_UNREADABLE"]
    if bad or scan:
        _add_detector_hits(states)
        notes = _notes(prep_notes, states, [])
        if bad:
            bad.sort(key=lambda st: st.status != "NOT_PR")  # stable: a real NOT_PR file is named first
            if bad[0].status == "NOT_PR":
                code = "NOT_A_PR"
                msg = messages.result_message(code, file=bad[0].name, why=bad[0].why, more=len(bad) - 1)
            else:  # only reviewer failures: a system problem, not the customer's files
                code = "REVIEW_FAILED"
                msg = messages.result_message(code)
            detail = f"{code} " + "; ".join(f"{st.name} ({st.why})" for st in bad)
            _finish(intake_dir, states, "REJECTED", code, msg, detail, notes)
            return 6, f"REJECTED {code} {len(bad)} of {len(states)} file(s)"
        first = scan[0]
        msg = messages.result_message("SCAN_UNREADABLE", file=first.name, page=first.page)
        detail = f"SCAN_UNREADABLE {first.name} page {first.page}"
        _finish(intake_dir, states, "REJECTED", "SCAN_UNREADABLE", msg, detail, notes)
        return 6, "REJECTED " + detail

    try:
        from_m, to_m = months.read_period(run_dir)
    except months.PeriodError:
        return 3, "ERROR input.json missing or invalid"
    kept, month_notes = months.assign_months([t for st in states for t in st.tables],
                                             {e.id: e.mtime for e in entries}, from_m, to_m)
    it = iter(kept)
    for st in states:  # assign_months keeps order and one output table per input table
        st.tables = [next(it) for _ in st.tables]
    _add_detector_hits(states)
    notes = _notes(prep_notes, states, month_notes)

    if not any(t.rows for t in kept):
        msg = messages.result_message("NOTHING_TO_DO_PERIOD", period=messages.format_period(from_m, to_m))
        line = f"NOTHING_TO_DO no PR files for {from_m}-{to_m}"
        _finish(intake_dir, states, "NOTHING_TO_DO", "NOTHING_TO_DO", msg, line, notes)
        return 7, line

    tables_dir = intake_dir / "tables"
    tables_dir.mkdir(exist_ok=True)
    for st in states:
        for n, t in enumerate(st.tables, 1):
            name = f"{st.entry.id}-{n}.csv" if len(st.tables) > 1 else f"{st.entry.id}.csv"
            _write_table(tables_dir / name, st, t)
    line = f"ACCEPTED {len(states)} file(s)"
    _finish(intake_dir, states, "ACCEPTED", "ACCEPTED", "", line, notes)
    return 0, line


def run(argv: list[str], ocr=None) -> tuple[int, str]:
    if len(argv) != 3 or argv[0] not in ("prepare", "verify"):
        return 1, USAGE
    try:
        if argv[0] == "verify":
            return verify(Path(argv[1]), Path(argv[2]))
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
