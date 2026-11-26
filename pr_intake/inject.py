"""Fixed-code detector for instruction-like text in extracted cells. Reports locations only."""
import re

from pr_intake.recipe import Table

CELL_CAP = 2000

_VERB = r"\b(delete|send|upload|run|execute|curl|wget|fetch|download|visit|click)\b"
# A link alone is normal (a vendor's web site): it counts only with an instruction verb within 40 characters.
_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"ignore (all |any |the )?(previous |prior |above )?instructions",
    r"disregard",
    r"system prompt",
    r"you are (an? )?(ai|assistant|model|claude)",
    _VERB + r".{0,40}\b(file|files|data|command|script|http)\b",
    _VERB + r".{0,40}https?://",
    r"https?://.{0,40}" + _VERB,
)]


def find_instruction_like(table: Table) -> list[str]:
    """Return "<location>, <field>" for each cell that looks like an instruction; never the text."""
    hits = []
    for row, loc in zip(table.rows, table.locations):
        for field, value in row.items():
            text = str(value)[:CELL_CAP]
            if any(p.search(text) for p in _PATTERNS):
                hits.append(f"{loc}, {field}")
    return hits
