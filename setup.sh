#!/usr/bin/env bash
# Installs what a cloud run needs. Run from the repo: bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"
# --ignore-installed: the image has Ubuntu's own cryptography/cffi (built for a
# different Python version). Without it, pip treats them as "already satisfied"
# and the azure import crashes with "No module named '_cffi_backend'". This
# installs PyPI copies into /usr/local/..., which comes first on sys.path.
python3 -m pip install -q --ignore-installed -r requirements.txt 2>/dev/null \
  || python3 -m pip install -q --ignore-installed --break-system-packages -r requirements.txt
# Fail here, loudly, if the import still doesn't work.
python3 -c "import azure.storage.blob, requests, openpyxl, pandas, pdfplumber, pypdfium2, PIL"

# tesseract-ocr reads scanned PDFs; LibreOffice recalculates the workbook's
# formulas for the zero-errors check. Best effort, one install each (tesseract
# first, so a LibreOffice failure cannot lose it): if either cannot be
# installed the run continues and says so.
if ! command -v soffice >/dev/null 2>&1 || ! command -v tesseract >/dev/null 2>&1; then
  SUDO=""
  [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo -n"
  timeout 300 $SUDO apt-get update -qq >/dev/null 2>&1 || true
  command -v tesseract >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive timeout 600 $SUDO apt-get install -y -qq --no-install-recommends tesseract-ocr >/dev/null 2>&1 || true
  command -v soffice >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive timeout 600 $SUDO apt-get install -y -qq --no-install-recommends libreoffice-calc >/dev/null 2>&1 || true
fi
# Last line: the LibreOffice answer, then the OCR answer.
LO="NOT available"; OCR="NOT available"
command -v soffice >/dev/null 2>&1 && LO="available"
command -v tesseract >/dev/null 2>&1 && OCR="available"
echo "SETUP OK (LibreOffice $LO; OCR $OCR)"
