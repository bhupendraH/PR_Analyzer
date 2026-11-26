import math
import re
from datetime import date, datetime, timedelta

_CUR = re.compile(r"^(?:rs\.?|inr|₹)", re.I)
_NUM = re.compile(r"^\d+(\.\d*)?$|^\.\d+$")


def parse_amount(s):
    """Return float, or None for blank; raise ValueError for non-numeric text."""
    if s is None:
        return None
    if isinstance(s, bool):
        raise ValueError(f"not an amount: {s!r}")
    if isinstance(s, (int, float)):
        return None if isinstance(s, float) and math.isnan(s) else float(s)
    t = str(s).strip()
    t = _CUR.sub("", t).strip()
    neg = False
    if t[-2:].lower() == "cr":
        neg, t = True, t[:-2]
    elif t[-2:].lower() == "dr":
        t = t[:-2]
    t = t.replace(",", "").replace(" ", "")
    if t.startswith("(") and t.endswith(")"):
        neg, t = not neg, t[1:-1]
    if t in ("", "-"):
        return None
    sign = 1.0
    if t[0] in "+-":
        sign = -1.0 if t[0] == "-" else 1.0
        t = t[1:]
    if not _NUM.match(t):
        raise ValueError(f"not an amount: {s!r}")
    v = sign * float(t)
    return -v if neg else v


_TIME = re.compile(r"[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?$")
_SERIAL = re.compile(r"^\d{5}(?:\.\d+)?$")
_FORMATS = ("%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%b-%y", "%d/%m/%y", "%d.%m.%y")


def parse_date(s):
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    if isinstance(s, str) and _SERIAL.match(s.strip()):
        s = float(s.strip())
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        if 20000 <= s <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=float(s))).date()
        return None
    if isinstance(s, str):
        t = _TIME.sub("", s.strip()).strip()
        for f in _FORMATS:
            try:
                return datetime.strptime(t, f).date()
            except ValueError:
                pass
    return None
