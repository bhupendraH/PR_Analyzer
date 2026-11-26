import re

_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_C = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def is_valid_gstin(s: str) -> bool:
    if not isinstance(s, str) or not _RE.match(s):
        return False
    total = 0
    for i, ch in enumerate(s[:14]):
        p = _C.index(ch) * (1 if i % 2 == 0 else 2)
        total += p // 36 + p % 36
    return _C[(36 - total % 36) % 36] == s[14]
