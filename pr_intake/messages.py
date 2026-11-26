"""Result messages for the backend (255-char agent_fail_reason). One table entry per outcome."""
import unicodedata

from pr_intake import limits

MAX_LEN = 255
NAME_MAX = 60

_ZIP_WHY = {
    "contains another zip": "nested zip",
    "password protected": "password protected",
    "too large when unpacked": "too large when unpacked",
    "unsafe paths": "unsafe paths",
    "cannot be opened": "damaged or unreadable",
}

# outcome -> template. Fields: {file} {page} {why} {more} {period}.
# {file} and {why} are the only parts truncated; guidance text never is.
_TEMPLATES = {
    "UNSUPPORTED_FILE": ("The PR folder contains a file type that cannot be read: {file}. "
                         "Supported: Excel (.xlsx), PDF, text (.txt, .csv) and .zip of these."),
    "ZIP_REJECTED": ("The zip file {file} cannot be opened safely ({why}). "
                     "Please upload the files directly."),
    "OCR_UNAVAILABLE": ("A scanned PDF was found but the scan reader is not available. "
                        "Please try again later or upload the Excel/exported file."),
    "TOO_LARGE": ("Too many files were uploaded (limit {max_files}) or they are too large in total "
                  "(limit {max_mb} MB)."),
    "TOO_MANY_SCAN_PAGES": ("Too many scanned pages were uploaded (limit {max_ocr} pages). "
                            "Please upload the Excel or exported files instead."),
    "NOTHING_TO_DO": "No Purchase Register files were found. Please upload the files and run again.",
}

_DROP = set("\x0b\x0c\x85  ")


def safe_name(s: str) -> str:
    chars = []
    for ch in s:
        if ch in _DROP:
            continue
        if ch.isspace():
            chars.append(" ")
        elif unicodedata.category(ch)[0] != "C":
            chars.append(ch)
    out = " ".join("".join(chars).split())
    return out if len(out) <= NAME_MAX else out[:NAME_MAX - 1] + "…"


def _cut(s: str, n: int) -> str:
    n = max(n, 1)
    return s if len(s) <= n else s[:n - 1] + "…"


def result_message(outcome: str, file: str = "", page: int | None = None, why: str = "",
                   more: int = 0, period: str = "") -> str:
    tpl = _TEMPLATES[outcome]
    if outcome == "ZIP_REJECTED":
        why = _ZIP_WHY.get(why, "damaged or unreadable")
    vals = dict(file=file, page=page, why=why, more=more, period=period,
                max_files=limits.MAX_FILES, max_mb=limits.MAX_TOTAL_BYTES // (1024 * 1024),
                max_ocr=limits.MAX_OCR_PAGES)
    msg = tpl.format(**vals)
    for key in ("file", "why"):
        over = len(msg) - MAX_LEN
        if over <= 0:
            break
        vals[key] = _cut(vals[key], len(vals[key]) - over)
        msg = tpl.format(**vals)
    return msg
