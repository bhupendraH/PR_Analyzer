"""Field table copied from instructions.txt STEP 2.1: field -> (default header, alternatives)."""
import re

FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "doc_ref": ("Doc Ref", ("Document Reference", "Voucher No", "Doc No")),
    "transaction_type": ("Transaction Type", ("Voucher Type", "Document Type")),
    "supplier_name": ("Party Name", ("Supplier Name", "Vendor Name")),
    "supplier_gstin": ("Party GSTIN", ("Supplier GSTIN", "Vendor GSTIN")),
    "supplier_state": ("Party Region", ("Supplier State", "Vendor State")),
    "supplier_invoice_no": ("ARAP", ("Supplier Invoice No", "Vendor Invoice No", "Bill No", "Invoice No")),
    "invoice_date": ("Doc Date", ("Invoice Date", "Bill Date", "Supplier Invoice Date")),
    "posting_date": ("Transaction Date", ("Posting Date", "Voucher Date", "GL Date")),
    "spend_value": ("Invoice Value", ("Assessable Value", "Base Amount")),
    "taxable": ("Taxable Value", ("Taxable Amount",)),
    "cgst": ("CGST Amount", ("CGST",)),
    "sgst": ("SGST Amount", ("SGST", "UTGST Amount", "UTGST")),
    "igst": ("IGST Amount", ("IGST",)),
    "invoice_total": ("Invoice Amount", ("Invoice Total", "Total Amount", "Gross Amount")),
    "reverse_charge": ("Reverse Charge", ("RCM", "Reverse Charge Applicable")),
    "account": ("Tariff/Account", ("GL Account", "Account", "Ledger", "Expense Head")),
    "hsn_sac": ("SAC/HSN", ("HSN/SAC", "HSN", "SAC", "HSN Code")),
    "entity_name": ("Office", ("Company", "Entity", "Branch")),
    "entity_gstin": ("Office_GSTIN", ("Company GSTIN", "Our GSTIN", "Buyer GSTIN")),
    "quantity": ("Quantity", ("Qty",)),
    "unit_rate": ("Rate", ("Unit Rate", "Unit Price")),
}

AMOUNT_FIELDS = ("spend_value", "taxable", "cgst", "sgst", "igst", "invoice_total", "quantity", "unit_rate")
DATE_FIELDS = ("invoice_date", "posting_date")


def normalise_header(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def match_fixed(headers: list[str]) -> dict[str, int]:
    """Map field -> column index; default header first, then alternatives in order."""
    norm = [normalise_header(h) for h in headers]
    out: dict[str, int] = {}
    for field, (default, alts) in FIELDS.items():
        for name in (default, *alts):
            n = normalise_header(name)
            if n in norm:
                out[field] = norm.index(n)
                break
    return out
