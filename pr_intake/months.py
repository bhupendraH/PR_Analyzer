"""Month of each PR row, range filter, and newest file per month (spec 8a)."""
import json
import re
from dataclasses import replace
from datetime import date
from pathlib import Path

from pr_intake.recipe import Table
from pr_intake.values import parse_date

_NAMES = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")
_NAME_MONTH = {}
for _i, _n in enumerate(_NAMES, 1):
    _NAME_MONTH[_n] = _i
    _NAME_MONTH[_n[:3]] = _i
_NAME_MONTH["sept"] = 9
_WORD = re.compile(r"(?<![a-z])(" + "|".join(sorted(_NAME_MONTH, key=len, reverse=True)) +
                   r")[\s._-]*((?:19|20)\d{2})(?!\d)", re.I)
_SEP = re.compile(r"(?<!\d)(0[1-9]|1[0-2])[-_]((?:19|20)\d{2})(?!\d)")
_SOLID = re.compile(r"(?<!\d)(0[1-9]|1[0-2])((?:19|20)\d{2})(?!\d)")
_MMYYYY = re.compile(r"\d{6}")


class PeriodError(Exception):
    """input.json missing or invalid."""


def month_from_name(name: str) -> str | None:
    """MMYYYY if the name holds exactly one distinct month and year, else None."""
    found = set()
    for m in _WORD.finditer(name):
        found.add(f"{_NAME_MONTH[m.group(1).lower()]:02d}{m.group(2)}")
    for rx in (_SEP, _SOLID):
        for m in rx.finditer(name):
            found.add(m.group(1) + m.group(2))
    return found.pop() if len(found) == 1 else None


def _valid_month(v) -> bool:
    return isinstance(v, str) and _MMYYYY.fullmatch(v) is not None and v.isascii() and 1 <= int(v[:2]) <= 12


def read_period(run_dir: Path) -> tuple[str, str]:
    d = Path(run_dir) / "input"
    try:
        files = [p for p in d.iterdir() if p.is_file() and p.name.lower() == "input.json"]
        data = json.loads(files[0].read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, IndexError):
        raise PeriodError("input.json missing or invalid") from None
    if not isinstance(data, dict):
        raise PeriodError("input.json missing or invalid")
    low = {str(k).lower(): v for k, v in data.items()}
    a, b = low.get("from_month"), low.get("to_month")
    if not (_valid_month(a) and _valid_month(b)) or (a[2:], a[:2]) > (b[2:], b[:2]):
        raise PeriodError("input.json missing or invalid")
    return a, b


def _base(source: str) -> str:
    return re.split(r"/", source.rsplit(" > ", 1)[-1])[-1]


def _key(m: str) -> tuple[str, str]:
    return (m[2:], m[:2])


def _id_key(fid: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", fid)]


def _row_month(t: Table, row: dict, sheet_m, file_m):
    if sheet_m or file_m:
        return sheet_m or file_m
    order = ["posting_date", "invoice_date"] if "posting_date" in t.fields else ["invoice_date"]
    for f in order:
        d = parse_date(row.get(f, ""))
        if isinstance(d, date):
            return f"{d.month:02d}{d.year}"
    return None


def _subset(t: Table, keep: list[int]) -> Table:
    return replace(t, rows=[t.rows[i] for i in keep], locations=[t.locations[i] for i in keep],
                   copied=[t.copied[i] for i in keep], ocr_rows=[t.ocr_rows[i] for i in keep])


def assign_months(tables, mtimes, from_month, to_month):
    notes: list[str] = []
    staged = []  # (table, kept indices, month per kept index)
    for t in tables:
        sheet_m = month_from_name(t.sheet) if t.sheet else None
        file_m = month_from_name(_base(t.source))
        keep, months, nomonth, outside = [], [], 0, 0
        for i, row in enumerate(t.rows):
            m = _row_month(t, row, sheet_m, file_m)
            if m is None:
                nomonth += 1
            elif not _key(from_month) <= _key(m) <= _key(to_month):
                outside += 1
            else:
                keep.append(i)
                months.append(m)
        if nomonth:
            notes.append(f"{t.source}: {nomonth} rows with no month dropped")
        if outside:
            notes.append(f"{t.source}: {outside} rows outside {from_month}-{to_month} dropped")
        staged.append((t, keep, months))

    winner: dict[str, tuple] = {}
    for t, _keep, months in staged:
        for m in set(months):
            cand = (mtimes.get(t.file_id, 0.0), _id_key(t.file_id), t.file_id, t.source)
            if m not in winner or cand[:2] > winner[m][:2]:
                winner[m] = cand

    out = []
    for t, keep, months in staged:
        final, final_m, lost = [], [], {}
        for i, m in zip(keep, months):
            if winner[m][2] == t.file_id:
                final.append(i)
                final_m.append(m)
            else:
                lost[m] = lost.get(m, 0) + 1
        for m in sorted(lost, key=_key):
            notes.append(f"{t.source}: {lost[m]} rows for {m} dropped, {winner[m][3]} is newer")
        n = _subset(t, final)
        n.rows = [dict(r, pr_period=f"{m[2:]}-{m[:2]}-01") for r, m in zip(n.rows, final_m)]
        n.fields = list(t.fields) + ([] if "pr_period" in t.fields else ["pr_period"])
        out.append(n)
    return out, notes
