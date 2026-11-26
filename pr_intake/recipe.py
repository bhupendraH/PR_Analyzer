"""Turn a reviewer verdict's recipe into a standard table; the fixed header list wins over the reviewer.

load_verdict returns the verdict dict plus one extra key, "_dropped_where": how many
instruction_like_text entries were dropped because `where` was not one of the allowed forms.
"""
import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from pr_intake.fields import AMOUNT_FIELDS, FIELDS, match_fixed, normalise_header
from pr_intake.gstin import is_valid_gstin

KINDS = ("purchase_register", "sales_register", "ledger", "gstr_2a_2b", "bank_statement", "invoice", "other")
LAYOUT_KIND = {"table": "xlsx", "delimited": "text", "fixed_width": "text", "positioned": "pdf"}
_SPEC = {"table": ("header",), "delimited": ("header",), "fixed_width": ("chars_from", "chars_to"),
         "positioned": ("x_from", "x_to")}
_TOP = {"file_id", "is_pr", "reason", "document_kind", "recipes", "instruction_like_text"}
_RECIPE = {"layout", "sheet", "delimiter", "header_rows", "data_rows", "skip_rows_matching", "columns",
           "group_fields", "continuation_of", "entity_from_header"}
_WHERE = re.compile(r"row \d+, column [A-Z]{1,3}|line \d+, chars \d+-\d+|page \d+, line \d+")
_ENTITY_SHAPES = ({"line", "chars_from", "chars_to"}, {"line", "x_from", "x_to"})


class VerdictError(Exception):
    """Verdict missing or malformed: the file is rejected as "could not be checked"."""


@dataclass
class Table:
    file_id: str
    source: str
    sheet: str | None
    fields: list[str]
    rows: list[dict[str, str]]
    locations: list[str]
    copied: list[list[str]]
    mapping: dict[str, str]
    entity: dict[str, str]
    skipped_rows: int
    recipe_rows: int
    notes: list[str]
    ocr_rows: list[bool]
    ocr_conf: dict[str, float]


def _need(ok, msg: str) -> None:
    if not ok:
        raise VerdictError(msg)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def _range_ok(a, b, ints: bool) -> bool:
    if ints:
        return _is_int(a) and _is_int(b) and a <= b
    nums = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (a, b))
    return nums and a <= b


def check_recipe(r) -> None:
    """Raise VerdictError unless r is a well-formed recipe."""
    _need(isinstance(r, dict), "recipe is not an object")
    _need(not set(r) - _RECIPE, "recipe has unknown keys")
    layout = r.get("layout")
    _need(layout in LAYOUT_KIND, "unknown recipe layout")
    _need(("sheet" in r) == (layout == "table"), "sheet is given for table layouts only, and always")
    _need(layout != "table" or isinstance(r["sheet"], str), "sheet is not text")
    _need(("delimiter" in r) == (layout == "delimited"), "delimiter is given for delimited layouts only, and always")
    _need(layout != "delimited" or (isinstance(r["delimiter"], str) and len(r["delimiter"]) == 1),
          "delimiter is not one character")
    hr = r.get("header_rows")
    _need(isinstance(hr, list) and all(_is_int(n) for n in hr), "header_rows is not a list of row numbers")
    _need(hr or layout in ("fixed_width", "positioned"), "header_rows is empty")
    dr = r.get("data_rows")
    _need(isinstance(dr, dict) and set(dr) == {"first", "last"} and _range_ok(dr["first"], dr["last"], True),
          "data_rows is not a valid first/last range")
    _need(not hr or dr["first"] > max(hr), "data_rows start before the header ends")
    skips = r.get("skip_rows_matching", [])
    _need(isinstance(skips, list) and all(isinstance(s, str) for s in skips), "skip_rows_matching is not a list")
    for s in skips:
        try:
            re.compile(s)
        except re.error:
            raise VerdictError("skip_rows_matching holds an invalid pattern") from None
    cols = r.get("columns")
    _need(isinstance(cols, dict), "columns is missing")
    _need(cols or layout in ("table", "delimited"), "columns is empty")  # ranges come only from the reviewer
    keys = _SPEC[layout]
    for f, spec in cols.items():
        _need(f in FIELDS, "columns names an unknown field")
        _need(isinstance(spec, dict) and set(spec) == set(keys), f"column spec for {f} does not fit the layout")
        if keys == ("header",):
            _need(isinstance(spec["header"], str) and spec["header"].strip(), f"header for {f} is empty")
        else:
            _need(_range_ok(spec[keys[0]], spec[keys[1]], layout == "fixed_width"), f"range for {f} is invalid")
    gf = r.get("group_fields", [])
    _need(isinstance(gf, list) and all(f in FIELDS for f in gf), "group_fields names an unknown field")
    _need(not set(gf) & set(AMOUNT_FIELDS), "group_fields may not hold amounts")
    co = r.get("continuation_of")
    _need(co is None or (co in FIELDS and co not in AMOUNT_FIELDS), "continuation_of is not a text field")
    efh = r.get("entity_from_header", {})
    _need(isinstance(efh, dict) and set(efh) <= {"entity_name", "entity_gstin"}, "entity_from_header is invalid")
    for spec in efh.values():
        _need(isinstance(spec, dict) and set(spec) in _ENTITY_SHAPES and _is_int(spec["line"]),
              "entity_from_header position is invalid")
        ints = "chars_from" in spec
        a, b = (spec["chars_from"], spec["chars_to"]) if ints else (spec["x_from"], spec["x_to"])
        _need(_range_ok(a, b, ints), "entity_from_header range is invalid")


def load_verdict(path: Path, file_id: str) -> dict:
    try:
        v = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise VerdictError("verdict is missing or not valid JSON") from None
    _need(isinstance(v, dict), "verdict is not an object")
    _need(not set(v) - _TOP, "verdict has unknown keys")
    _need(_TOP - {"instruction_like_text"} <= set(v), "verdict is missing keys")
    _need(v["file_id"] == file_id, "verdict is for another file")
    _need(isinstance(v["is_pr"], bool), "is_pr is not true/false")
    _need(isinstance(v["reason"], str), "reason is not text")
    _need(v["document_kind"] in KINDS, "document_kind is not in the list")
    _need(v["is_pr"] == (v["document_kind"] == "purchase_register"), "is_pr and document_kind disagree")
    recipes = v["recipes"]
    _need(isinstance(recipes, list), "recipes is not a list")
    if v["is_pr"]:
        _need(recipes, "a purchase register verdict has no recipes")
        for r in recipes:
            check_recipe(r)
        sheets = [r.get("sheet") for r in recipes]
        _need(len(recipes) == 1 or (all(r["layout"] == "table" for r in recipes) and len(set(sheets)) == len(sheets)),
              "several recipes must be table layouts on different sheets")
    else:
        recipes = []
    ilt = v.get("instruction_like_text", [])
    _need(isinstance(ilt, list), "instruction_like_text is not a list")
    kept = [{"where": e["where"]} for e in ilt
            if isinstance(e, dict) and isinstance(e.get("where"), str) and _WHERE.fullmatch(e["where"])]
    return dict(v, reason=v["reason"][:200], recipes=recipes, instruction_like_text=kept,
                _dropped_where=len(ilt) - len(kept))


def _q(s: str) -> str:
    """File text quoted for a note: one line, at most 40 characters."""
    return '"' + re.sub(r"\s+", " ", str(s)).strip()[:40] + '"'


def _join(cells) -> str:
    return " ".join(str(c).strip() for c in cells if str(c).strip())


def _reader(extracted: dict, recipe: dict):
    """(count, get) where get(n) -> (record, skip_text, location, page) for row/line n (1-based)."""
    layout = recipe["layout"]
    if layout == "table":
        name = recipe["sheet"]
        sh = next((s for s in extracted["sheets"] if s["name"] == name), None)
        _need(sh is not None, "the recipe's sheet is not in the file")
        rows = sh["rows"]
        return len(rows), lambda n: (rows[n - 1], _join(rows[n - 1]), f"{name}!R{n}", None)
    lines = extracted["lines"]
    if layout == "delimited":
        def get(n):
            cells = next(csv.reader([lines[n - 1].replace("\0", "")], delimiter=recipe["delimiter"], quotechar='"'), [])
            return cells, _join(cells), f"L{n}", None
        return len(lines), get
    if layout == "fixed_width":
        return len(lines), lambda n: (lines[n - 1], lines[n - 1], f"L{n}", None)

    def get_pdf(n):
        ln = lines[n - 1]
        return ln["words"], _join(w["text"] for w in ln["words"]), f"p{ln['page']}:L{ln['n']}", ln["page"]
    return len(lines), get_pdf


def _cell(layout: str, rec, key) -> tuple[str, list[float]]:
    if layout in ("table", "delimited"):
        return (str(rec[key]).strip() if key < len(rec) else ""), []
    a, b = key
    if layout == "fixed_width":
        return rec[a - 1:b].strip(), []
    ws = sorted((w for w in rec if a <= (w["x0"] + w["x1"]) / 2 <= b), key=lambda w: w["x0"])
    return " ".join(w["text"] for w in ws).strip(), [w["conf"] for w in ws if w.get("conf") is not None]


def _range_key(spec: dict):
    return (spec["chars_from"], spec["chars_to"]) if "chars_from" in spec else (spec["x_from"], spec["x_to"])


def _map_columns(layout, cols, header_recs, notes):
    """Return field -> (column key, mapping label). Fixed header list first, reviewer fills the rest."""
    by_header = layout in ("table", "delimited")
    if by_header:
        keys = list(range(max((len(r) for r in header_recs), default=0)))
    else:
        keys = list(dict.fromkeys(_range_key(s) for s in cols.values()))
    htext = {k: " ".join(t for t in (_cell(layout, r, k)[0] for r in header_recs) if t) for k in keys}
    unit = "chars" if layout == "fixed_width" else "x"

    def desc(k):
        return _q(htext[k]) if by_header else f"{unit} {k[0]:g}-{k[1]:g}"
    out = {f: (keys[i], "fixed:" + htext[keys[i]]) for f, i in match_fixed([htext[k] for k in keys]).items()}
    fixed_by_key = {k: f for f, (k, _) in out.items()}
    for f, spec in cols.items():
        if by_header:
            k = _find_header(spec["header"], keys, htext)
            if k is None:
                notes.append(f"{f}: reviewer header {_q(spec['header'])} not found in the header row; dropped")
                continue
            label = "reviewer:" + spec["header"]
        else:
            k = _range_key(spec)
            label = f"reviewer:{desc(k)}"
        if f in out:
            if out[f][0] != k:
                notes.append(f"{f}: reviewer chose {desc(k)}, fixed list chose {desc(out[f][0])}; fixed list used (disagree)")
        elif k in fixed_by_key:
            notes.append(f"{f}: reviewer chose {desc(k)}, fixed list reads it as {fixed_by_key[k]}; dropped (disagree)")
        else:
            out[f] = (k, label)
    return out


def _find_header(h: str, keys, htext):
    flat = lambda t: re.sub(r"\r\n|\r|\n", "\\\\n", t).strip()  # noqa: E731 - preview shows newlines as \n
    for norm in (str.strip, flat, normalise_header):
        want = norm(h)
        hit = next((k for k in keys if want and norm(htext[k]) == want), None)
        if hit is not None:
            return hit
    return None


def _entity(extracted, recipe, count, notes) -> dict[str, str]:
    efh = recipe.get("entity_from_header") or {}
    layout = recipe["layout"]
    if efh and layout == "table":
        notes.append("entity_from_header ignored for a table layout")
        return {}
    out = {}
    for f, spec in efh.items():
        if ("x_from" in spec) != (layout == "positioned"):
            notes.append(f"{f}: entity_from_header position does not fit the layout; ignored")
            continue
        n = spec["line"]
        if n > count:
            notes.append(f"{f}: entity_from_header line {n} is past the end; ignored")
            continue
        rec = extracted["lines"][n - 1]
        val = _cell(layout, rec["words"] if layout == "positioned" else rec, _range_key(spec))[0]
        if f == "entity_gstin" and not is_valid_gstin(val):
            notes.append(f"entity_gstin read from header line {n} is not a valid GSTIN; dropped")
        elif not val:
            notes.append(f"{f}: entity_from_header line {n} is blank there; dropped")
        else:
            out[f] = val
    return out


def apply_recipe(extracted: dict, recipe: dict, file_id: str, source: str, ocr_pages: list[int]) -> Table:
    check_recipe(recipe)
    layout = recipe["layout"]
    _need(extracted.get("kind") == LAYOUT_KIND[layout], f"a {layout} recipe does not fit this file")
    notes: list[str] = []
    count, get = _reader(extracted, recipe)
    _need(all(n <= count for n in recipe["header_rows"]), "a header row is past the end of the data")
    cols = _map_columns(layout, recipe["columns"], [get(n)[0] for n in recipe["header_rows"]], notes)
    fields = [f for f in FIELDS if f in cols]

    co = recipe.get("continuation_of")
    if co and co not in cols:
        notes.append(f"continuation_of field {co} is not mapped; ignored")
        co = None
    gf = [f for f in recipe.get("group_fields", []) if f in cols]
    if len(gf) != len(recipe.get("group_fields", [])):
        notes.append("group_fields that are not mapped were ignored")
    if gf and "doc_ref" not in cols:
        notes.append("group_fields ignored: doc_ref is not mapped")
        gf = []

    first, last = recipe["data_rows"]["first"], recipe["data_rows"]["last"]
    if last > count:
        notes.append(f"data_rows.last {last} is past the end ({count}); rows after {count} not counted")
    skips = [re.compile(p) for p in recipe.get("skip_rows_matching", [])]
    rows, locs, copied, ocr_rows, ocr_conf = [], [], [], [], {}
    skipped, group = 0, None
    for n in range(first, min(last, count) + 1):
        rec, text, loc, page = get(n)
        if not text.strip() or any(p.search(text) for p in skips):
            skipped += 1
            continue
        cells = {f: _cell(layout, rec, cols[f][0]) for f in fields}
        vals = {f: c[0] for f, c in cells.items()}
        if co and rows and vals[co] and not any(v for f, v in vals.items() if f != co):
            rows[-1][co] = (rows[-1][co] + " " + vals[co]).strip()
            skipped += 1
            continue
        cp = []
        if gf:
            if vals["doc_ref"]:
                group = {f: vals[f] for f in gf}
            elif group:
                for f in gf:
                    if not vals[f] and group[f]:
                        vals[f] = group[f]
                        cp.append(f)
        is_ocr = page is not None and page in ocr_pages
        if is_ocr:
            confs = [c for f in (*AMOUNT_FIELDS, "supplier_gstin") if f in cells for c in cells[f][1]]
            if confs:
                ocr_conf[loc] = min(confs)
        rows.append(vals)
        locs.append(loc)
        copied.append(cp)
        ocr_rows.append(is_ocr)

    if layout == "positioned":
        for p in ocr_pages:
            confs = [w["conf"] for ln in extracted["lines"] if ln["page"] == p
                     for w in ln["words"] if w.get("conf") is not None]
            if confs:
                ocr_conf[f"page:{p}"] = mean(confs)

    return Table(file_id=file_id, source=source, sheet=recipe.get("sheet"), fields=fields, rows=rows,
                 locations=locs, copied=copied, mapping={f: cols[f][1] for f in fields},
                 entity=_entity(extracted, recipe, count, notes), skipped_rows=skipped,
                 recipe_rows=last - first + 1, notes=notes, ocr_rows=ocr_rows, ocr_conf=ocr_conf)


def apply_verdict(extracted: dict, verdict: dict, file_id: str, source: str, ocr_pages: list[int]) -> list[Table]:
    """One Table per recipe (one per sheet for multi-sheet workbooks)."""
    return [apply_recipe(extracted, r, file_id, source, ocr_pages) for r in verdict["recipes"]]
