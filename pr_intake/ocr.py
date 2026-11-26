"""Tesseract OCR for scanned PDF pages. Output coordinates are PDF points."""
import shutil
import subprocess
import tempfile
from pathlib import Path

from pr_intake import limits
from pr_intake.extract import OcrUnavailable


class OcrEngine:
    def available(self) -> bool:
        raise NotImplementedError

    def page_words(self, page, page_no: int) -> list[dict]:
        raise NotImplementedError


def render_page(pdf_path, page_index: int, out_png) -> None:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        bitmap = doc[page_index].render(scale=limits.OCR_DPI / 72)
        bitmap.to_pil().save(str(out_png), "PNG")
    finally:
        doc.close()


def parse_tsv(text: str) -> list[dict]:
    pt = 72 / limits.OCR_DPI
    words: list[dict] = []
    lines = text.splitlines()
    if not lines:
        return words
    header = lines[0].split("\t")
    for row in lines[1:]:
        cols = row.split("\t")
        if len(cols) < len(header):
            continue
        r = dict(zip(header, cols))
        try:
            if int(r["level"]) != 5:
                continue
            conf = float(r["conf"])
            left, top, width = float(r["left"]), float(r["top"]), float(r["width"])
            ids = {k: int(r[k]) for k in ("block_num", "par_num", "line_num")}
        except (KeyError, ValueError):
            continue
        t = r["text"].strip()
        if conf == -1 or not t:
            continue
        words.append({"x0": left * pt, "x1": (left + width) * pt, "top": top * pt,
                      "text": t, "conf": conf, **ids})
    return words


class TesseractEngine(OcrEngine):
    def available(self) -> bool:
        return shutil.which("tesseract") is not None

    def read_page(self, png_path) -> list[dict]:
        cmd = ["tesseract", str(png_path), "stdout", *limits.OCR_ARGS, "tsv"]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode != 0:
            raise OSError("tesseract failed")
        return parse_tsv(r.stdout.decode("utf-8", errors="replace"))

    def page_words(self, page, page_no: int) -> list[dict]:
        try:
            pdf_path = Path(page.pdf.stream.name)
            with tempfile.TemporaryDirectory() as td:
                png = Path(td) / "page.png"
                render_page(pdf_path, page_no - 1, png)
                return self.read_page(png)
        except Exception:
            raise OcrUnavailable(page_no) from None
