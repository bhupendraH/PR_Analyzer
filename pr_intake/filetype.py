import io
import zipfile
from typing import Literal

_ALLOWED_CTRL = {"\t", "\r", "\n", "\f"}


def decode_text(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8-sig"
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le"), "utf-16-le"
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be"), "utf-16-be"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def sniff(data: bytes) -> Literal["xlsx", "zip", "pdf", "text", "unsupported"]:
    if not data:
        return "unsupported"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return "xlsx" if "xl/workbook.xml" in z.namelist() else "zip"
        except zipfile.BadZipFile:
            return "unsupported"
    if data.startswith(b"%PDF-"):
        return "pdf"
    try:
        text, _ = decode_text(data)
    except UnicodeDecodeError:
        return "unsupported"
    sample = text[:65536]
    if not sample:
        return "unsupported"
    bad = sum(1 for c in sample if (ord(c) < 32 and c not in _ALLOWED_CTRL) or ord(c) == 127)
    return "text" if bad / len(sample) < 0.01 else "unsupported"
