"""Evidence checks on a built Table: the data must back the reviewer's column mapping.

A check that needs an unmapped field is omitted, except required_fields which fails.
NOTE: the spec's OCR check "page amounts sum to the printed page total" is NOT
implemented, because the recipe schema has no way to mark a total row.
"""
import re
from collections import OrderedDict
from dataclasses import dataclass

from pr_intake import limits
from pr_intake.gstin import is_valid_gstin
from pr_intake.recipe import Table
from pr_intake.values import parse_amount, parse_date

_CUSTOMER = re.compile(r"customer|buyer|client|billto|shipto")
_TAX = ("cgst", "sgst", "igst")
_MONEY = ("spend_value", "taxable", *_TAX, "invoice_total")  # not quantity / unit_rate


@dataclass
class Check:
    name: str
    passed: bool
    code: str
    where: str = ""  # OCR checks: first failing location ("p<page>:L<n>", "page:<n>" or a row location)


def _share_ok(good: int, total: int, minimum: float) -> bool:
    return total == 0 or good / total >= minimum


def _amt(v):
    try:
        return parse_amount(v)
    except ValueError:
        return None


def _num(v) -> float:
    return _amt(v) or 0.0


def _gstin_ok(v: str) -> bool:
    return is_valid_gstin(v.upper().replace(" ", ""))


def _gstin_values(t: Table):
    for i, r in enumerate(t.rows):
        v = r.get("supplier_gstin", "").strip()
        if v and v.upper() != "UNREGISTERED":
            yield i, v


def _documents(t: Table) -> "OrderedDict[str, list[int]]":
    docs: OrderedDict[str, list[int]] = OrderedDict()
    for i, r in enumerate(t.rows):
        ref = r.get("doc_ref", "").strip()
        if ref:
            docs.setdefault(ref, []).append(i)
    return docs


def _arith_docs(t: Table) -> list[tuple[list[int], bool]]:
    """Eligible documents: (row indexes, within tolerance)."""
    out = []
    for idxs in _documents(t).values():
        first = t.rows[idxs[0]]
        if not first.get("invoice_total", "").strip() or _amt(first["invoice_total"]) is None:
            continue
        if "transaction_type" in t.fields and first.get("transaction_type", "").strip().lower() == "journal":
            continue
        taxable = sum(_num(t.rows[i].get("taxable")) for i in idxs)
        tax = sum(_num(t.rows[i].get(f)) for i in idxs for f in _TAX)
        if taxable == 0 and tax == 0:  # no tax breakdown recorded: cannot be checked arithmetically
            continue
        s = taxable + tax
        out.append((idxs, abs(s - _num(first["invoice_total"])) <= limits.ARITH_TOL))
    return out


def _page_of(loc: str) -> int:
    """Page number of an OCR location ("page:<n>" or "p<n>:L<m>"); 0 when it holds none."""
    mt = re.fullmatch(r"page:(\d+)|p(\d+):L\d+", loc)
    return int(mt.group(1) or mt.group(2)) if mt else 0


def _header_text(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.split(":", 1)[-1].lower())


def run_checks(t: Table) -> list[Check]:
    m = set(t.mapping)
    checks: list[Check] = []

    def add(name, ok, code, where=""):
        checks.append(Check(name, bool(ok), code, "" if ok else where))

    def coverage():
        add("rows_found", len(t.rows) >= 1, "no_rows")
        add("row_coverage", len(t.rows) + t.skipped_rows == t.recipe_rows, "rows_dropped")

    ok_req = ("doc_ref" in m and ("supplier_name" in m or "supplier_gstin" in m)
              and "invoice_date" in m and ("spend_value" in m or "taxable" in m))
    add("required_fields", ok_req, "missing_fields")
    if not ok_req:
        coverage()
        return checks
    coverage()

    n = len(t.rows)
    add("doc_ref", _share_ok(sum(1 for r in t.rows if r.get("doc_ref", "").strip()), n, limits.DOC_REF_MIN),
        "doc_ref_blank")

    if "supplier_gstin" in m:
        vals = [v for _, v in _gstin_values(t)]
        add("supplier_gstin", _share_ok(sum(_gstin_ok(v) for v in vals), len(vals), limits.GSTIN_MIN), "gstin_invalid")

    dv = [r["invoice_date"].strip() for r in t.rows if "invoice_date" in m and r.get("invoice_date", "").strip()]
    add("dates", _share_ok(sum(parse_date(v) is not None for v in dv), len(dv), limits.DATE_MIN), "dates_invalid")

    av = [r[f] for r in t.rows for f in _MONEY if f in m and r.get(f, "").strip()]
    add("amounts", _share_ok(sum(_amt(v) is not None for v in av), len(av), limits.AMOUNT_MIN), "amounts_invalid")

    arith_possible = {"taxable", "invoice_total"} <= m and bool(m & set(_TAX))
    if arith_possible:
        docs = _arith_docs(t)
        add("arithmetic", _share_ok(sum(g for _, g in docs), len(docs), limits.ARITH_MIN), "totals_mismatch")

    if "spend_value" in m:
        same = "invoice_total" in m and t.mapping["spend_value"] == t.mapping["invoice_total"]
        incl = False
        if not same and "taxable" in m and m & set(_TAX):
            lines = []
            for r in t.rows:
                tax = sum(_num(r.get(f)) for f in _TAX)
                if tax != 0:
                    lines.append(abs(_num(r.get("spend_value")) - (_num(r.get("taxable")) + tax)) <= limits.ARITH_TOL)
            incl = bool(lines) and sum(lines) / len(lines) >= limits.SPEND_TAX_INCL_MAX
        add("spend_not_tax_inclusive", not (same or incl), "spend_includes_tax")

    add("tax_evidence", bool(m & {"taxable", *_TAX}), "no_tax_columns")

    sides = [f for f in ("supplier_name", "supplier_gstin") if f in m]
    bad_side = any(_CUSTOMER.search(_header_text(t.mapping[f])) for f in sides)
    bad_side = bad_side or any("sale" in _header_text(t.mapping[f]) for f in ("doc_ref", "transaction_type") if f in m)
    if "transaction_type" in m:
        tt = [_header_text(r.get("transaction_type", "")) for r in t.rows if r.get("transaction_type", "").strip()]
        bad_side = bad_side or (bool(tt) and sum("sale" in v for v in tt) / len(tt) >= 0.5)
    add("purchase_side", not bad_side, "customer_side")

    if any(t.ocr_rows):
        bad = [k for k, v in t.ocr_conf.items()
               if v < (limits.OCR_MIN_MEAN_CONF if k.startswith("page:") else limits.OCR_MIN_CELL_CONF)]
        bad.sort(key=_page_of)  # stable: lowest page first, insertion order within a page
        add("ocr_confidence", not bad, "scan_low_confidence", bad[0] if bad else "")
        if "supplier_gstin" in m:
            gbad = [t.locations[i] for i, v in _gstin_values(t) if t.ocr_rows[i] and not _gstin_ok(v)]
            add("ocr_gstin", not gbad, "scan_gstin_invalid", gbad[0] if gbad else "")
        if arith_possible:
            tbad = [t.locations[idxs[0]] for idxs, g in _arith_docs(t) if t.ocr_rows[idxs[0]] and not g]
            add("ocr_totals", not tbad, "scan_totals_mismatch", tbad[0] if tbad else "")
    return checks
