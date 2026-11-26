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
    # NOTHING_TO_DO (no period) is used by prepare; NOTHING_TO_DO_PERIOD by the runner (spec section 9).
    "NOTHING_TO_DO": "No Purchase Register files were found. Please upload the files and run again.",
    "NOTHING_TO_DO_PERIOD": ("No Purchase Register files were found for {period}. "
                             "Please upload the files and run again."),
    "SCAN_UNREADABLE": ("A scanned page could not be read reliably: {file}, page {page}. "
                        "Please upload a clearer scan or the Excel/exported file."),
    "NOT_A_PR": ("The PR folder contains files that do not look like a Purchase Register: "
                 "{file} ({why}){and_more}. Please remove or replace them and run again."),
}

KIND_LABELS = {
    "sales_register": "looks like a sales register",
    "ledger": "looks like a ledger",
    "gstr_2a_2b": "looks like a GSTR-2A/2B",
    "bank_statement": "looks like a bank statement",
    "invoice": "looks like an invoice",
    "other": "not recognised as a Purchase Register",
    "purchase_register": "not recognised as a Purchase Register",
}

_NOT_PR = "the columns a Purchase Register needs were not found"
CHECK_SENTENCES = {
    "missing_fields": _NOT_PR,
    "no_rows": "no data rows were found",
    "doc_ref_blank": "most rows have no document reference",
    "gstin_invalid": "the column taken as supplier GSTIN mostly does not hold valid GSTINs",
    "dates_invalid": "the invoice dates could not be read",
    "amounts_invalid": "the amount columns hold values that are not numbers",
    "totals_mismatch": "taxable value plus tax does not match the invoice totals",
    "spend_includes_tax": "the value column includes tax",
    "customer_side": "it looks like a sales (customer-side) register",
    "rows_dropped": "some rows could not be read",
    "no_tax_columns": "no tax columns were found",
    "scan_low_confidence": "a scanned page could not be read reliably",
    "scan_gstin_invalid": "a scanned page could not be read reliably",
    "scan_totals_mismatch": "a scanned page could not be read reliably",
    "unchecked": "could not be checked",
}

_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_period(from_month: str, to_month: str) -> str:
    """MMYYYY -> "Feb 2025 to Apr 2025", or "Feb 2025" when both are the same month."""
    def one(m):
        return f"{_MON[int(m[:2]) - 1]} {m[2:]}"
    a, b = one(from_month), one(to_month)
    return a if a == b else f"{a} to {b}"

_SHELL_CHARS = set(chr(34) + chr(39) + "$`!" + chr(92))
_DROP = set("\x0b\x0c\x85  ")


def safe_name(s: str) -> str:
    chars = []
    for ch in s:
        if ch in _DROP:
            continue
        if ch in _SHELL_CHARS:
            chars.append("_")
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
    vals = dict(and_more=f" and {more} more" if more > 0 else "", file=file, page=page, why=why, more=more, period=period,
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
