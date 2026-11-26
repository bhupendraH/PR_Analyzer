"""manifest.json: the intake step's run-level decision and per-file results."""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class FileResult:
    id: str
    source: str
    kind: str
    pages: int | None
    ocr_pages: list[int]
    accepted: bool
    verdict: str  # "PR" | "NOT_PR" | "UNCHECKED"
    document_kind: str
    reason_code: str
    reviewer_reason: str
    checks: list[dict]
    instruction_like: list[str]


@dataclass
class Manifest:
    decision: str  # "ACCEPTED" | "REJECTED" | "NOTHING_TO_DO"
    outcome: str
    result_message: str
    detail: str
    notes: list[str] = field(default_factory=list)
    files: list[FileResult] = field(default_factory=list)
    credit_used: int = 0


def write_manifest(path, m: Manifest) -> None:
    m.credit_used = sum(1 for f in m.files if f.accepted)
    Path(path).write_text(json.dumps(asdict(m), indent=2, ensure_ascii=False), encoding="utf-8")


def read_manifest(path) -> Manifest:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    d["files"] = [FileResult(**f) for f in d.get("files", [])]
    return Manifest(**d)
