"""Extract xlsx, text and PDF text-layer content and render reviewer previews."""
import datetime
import re
from typing import Any

from pr_intake import limits
from pr_intake.filetype import decode_text


class ExtractError(Exception):
    """File cannot be read (corrupt or password protected)."""


class OcrUnavailable(Exception):
    """A PDF page needs OCR but no OCR engine was supplied."""

    def __init__(self, page: int):
        super().__init__(f"page {page} needs OCR")
        self.page = page


class TooManyOcrPages(Exception):
    """A single file needs more OCR pages than limits.MAX_OCR_PAGES."""


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime.datetime):
        if v.time() == datetime.time(0):
            return v.date().isoformat()
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v == int(v) and abs(v) < 1e15 else repr(v)
    return str(v)


def _extract_xlsx(path: str) -> dict:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            sheets = []
            for ws in wb.worksheets:
                ws.reset_dimensions()
                rows = [[_cell(c) for c in r] for r in ws.iter_rows(min_row=1, values_only=True)]
                while rows and not any(rows[-1]):
                    rows.pop()
                sheets.append({"name": ws.title, "rows": rows})
        finally:
            wb.close()
    except Exception:
        raise ExtractError("cannot be read") from None
    return {"kind": "xlsx", "sheets": sheets}


def _extract_text(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            text, enc = decode_text(f.read())
    except Exception:
        raise ExtractError("cannot be read") from None
    lines = re.split(r"\r\n|\r|\n", text)
    if lines and lines[-1] == "":
        lines.pop()
    return {"kind": "text", "encoding": enc, "lines": lines}


def _ocr_words(page: Any, page_no: int, ocr: Any) -> list[dict]:
    """OCR path for one page; isolated so the OCR engine can be wired in here.

    Returns word dicts with keys x0, x1, top, text, conf.
    """
    if ocr is None or not ocr.available():
        raise OcrUnavailable(page_no)
    return ocr.page_words(page, page_no)


def _extract_pdf(path: str, ocr: Any) -> dict:
    import pdfplumber
    lines: list[dict] = []
    ocr_pages: list[int] = []
    try:
        with pdfplumber.open(path) as pdf:
            total = len(pdf.pages)
            for pno, page in enumerate(pdf.pages, 1):
                words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
                chars = sum(len(w["text"]) for w in words)
                if chars < limits.SCAN_TEXT_MIN_CHARS and page.images:
                    ocr_pages.append(pno)
                    if len(ocr_pages) > limits.MAX_OCR_PAGES:
                        raise TooManyOcrPages()
                    words = _ocr_words(page, pno, ocr)
                groups: dict[int, list[dict]] = {}
                for w in words:
                    groups.setdefault(round(w["top"] / 3), []).append(w)
                for key in sorted(groups):
                    ws = sorted(groups[key], key=lambda w: w["x0"])
                    lines.append({
                        "n": len(lines) + 1,
                        "page": pno,
                        "words": [{"x0": float(w["x0"]), "x1": float(w["x1"]), "text": w["text"],
                                   "conf": w.get("conf")} for w in ws],
                    })
    except (OcrUnavailable, TooManyOcrPages):
        raise
    except Exception:
        raise ExtractError("cannot be read") from None
    return {"kind": "pdf", "pages": total, "ocr_pages": ocr_pages, "lines": lines}


def extract(entry: Any, ocr: Any = None) -> dict:
    if entry.kind == "xlsx":
        return _extract_xlsx(entry.local_path)
    if entry.kind == "text":
        return _extract_text(entry.local_path)
    if entry.kind == "pdf":
        return _extract_pdf(entry.local_path, ocr)
    raise ExtractError("cannot be read")


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _flat(s: str) -> str:
    return re.sub(r"\r\n|\r|\n", "\\n", s)


def _window(total: int) -> tuple[list[int], int]:
    """Indices (0-based) to show, and number omitted."""
    head, tail = limits.PREVIEW_HEAD, limits.PREVIEW_TAIL
    if total <= head + tail:
        return list(range(total)), 0
    return list(range(head)) + list(range(total - tail, total)), total - head - tail


def _emit(out: list[str], total: int, fmt) -> None:
    shown, omitted = _window(total)
    for i in shown:
        if omitted and i == total - limits.PREVIEW_TAIL:
            out.append(f"... {omitted} rows omitted ...")
        fmt(out, i)


def render_preview(extracted: dict, source: str) -> str:
    kind = extracted["kind"]
    out = [f"FILE: {source} | KIND: {kind}"]
    if kind == "xlsx":
        for sh in extracted["sheets"]:
            rows = sh["rows"]
            total = len(rows)
            width = max([len(r) for r in rows] + [1])
            out.append(f"SHEET: {_flat(sh['name'])}")
            out.append("COLS | " + " | ".join(_col_letter(i) for i in range(width)))
            _emit(out, total, lambda o, i, rows=rows: o.append(
                f"R{i + 1} | " + " | ".join(_flat(c) for c in rows[i])))
            out.append(f"ROWS: {total}")
    elif kind == "text":
        lines = extracted["lines"]
        total = len(lines)
        w = max(4, len(str(total)))
        shown, _ = _window(total)
        mx = min(max([len(lines[i]) for i in shown] + [10]), 200)
        digits = ("1234567890" * (mx // 10 + 1))[:mx]
        ruler = " " * (w + 1) + " | " + digits
        state = {"last": None}

        def fmt(o, i):
            if i % 50 == 0 or not state["last"]:
                o.append(ruler)
                state["last"] = True
            o.append(f"L{i + 1:0{w}d} | {lines[i]}")
        if not total:
            out.append(ruler)
        _emit(out, total, fmt)
        out.append(f"ROWS: {total}")
    else:
        lines = extracted["lines"]
        total = len(lines)
        w = max(4, len(str(total)))

        def fmt(o, i):
            ln = lines[i]
            words = " ".join(f"{x['text']}@{round(x['x0'])}" for x in ln["words"])
            o.append(f"L{ln['n']:0{w}d} p{ln['page']} | {words}")
        _emit(out, total, fmt)
        out.append(f"ROWS: {total}")
    return "\n".join(out) + "\n"
