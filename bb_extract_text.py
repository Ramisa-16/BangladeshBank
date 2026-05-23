#!/usr/bin/env python3
"""
Bangladesh Bank Circular PDF Text Extractor v2
================================================
- Extracts text from all PDFs in downloads/
- For scanned/image PDFs: uses OCR (Tesseract, ben+eng)
- For Bijoy-encoded Bangla PDFs: detects garbled text and OCRs those too
- For normal Unicode PDFs: extracts directly (fast)
- Saves .txt files to extracted_text/ mirroring dept/year structure
- Skips already-extracted files (safe to re-run)

Requirements:
    pip install pymupdf pytesseract pillow tqdm
    sudo apt-get install tesseract-ocr tesseract-ocr-ben   (Linux)
    Windows: install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki
             then add to PATH, also download ben.traineddata

Usage:
    python bb_extract_text.py                  # all PDFs
    python bb_extract_text.py --dept brpd      # only BRPD
    python bb_extract_text.py --year 2024      # only 2024
    python bb_extract_text.py --workers 2      # parallel (keep low for OCR)
    python bb_extract_text.py --no-ocr         # skip OCR, text PDFs only
"""

import argparse
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

DOWNLOADS_DIR  = Path("downloads")
EXTRACTED_DIR  = Path("extracted_text")
WORKERS        = 2      # keep low — OCR is CPU-heavy
OCR_DPI        = 200    # higher = better quality but slower
OCR_LANG       = "ben+eng"
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Users\Ramisa\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ── Dependency check ────────────────────────────────────────────────────────────
def check_deps(need_ocr=True):
    missing = []
    for mod, pkg in [("fitz", "pymupdf"), ("tqdm", "tqdm")]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if need_ocr:
        for mod, pkg in [("pytesseract", "pytesseract"), ("PIL", "pillow")]:
            try:
                __import__(mod)
            except ImportError:
                missing.append(pkg)
    if missing:
        print(f"[ERROR] Missing packages: pip install {' '.join(missing)}")
        sys.exit(1)
    if need_ocr:
        try:
            import pytesseract
            langs = pytesseract.get_languages()
            if "ben" not in langs:
                print("[WARNING] Bangla OCR language not found.")
                print("  Linux:   sudo apt-get install tesseract-ocr-ben")
                print("  Windows: download ben.traineddata from:")
                print("           https://github.com/tesseract-ocr/tessdata/raw/main/ben.traineddata")
                print("           Place it in your Tesseract tessdata folder")
                print("  Falling back to English OCR only.\n")
                global OCR_LANG
                OCR_LANG = "eng"
        except Exception as e:
            print(f"[ERROR] Tesseract not found: {e}")
            print("  Install from: https://github.com/UB-Mannheim/tesseract/wiki")
            sys.exit(1)

check_deps_done = False


# ── Text quality detection ───────────────────────────────────────────────────────
def is_bijoy_encoded(text: str) -> bool:
    """Detect Bijoy/SutonnyMJ legacy Bangla font encoding (looks like garbled ASCII)."""
    if not text or len(text) < 20:
        return False
    # Bijoy-encoded text has unusual combinations of these characters
    bijoy_markers = ['‡', '†', 'ÿ', 'û', 'ü', '÷', 'ô', 'ó']
    marker_count = sum(text.count(m) for m in bijoy_markers)
    return marker_count > len(text) * 0.015  # >1.5% of chars are bijoy markers


def is_good_text(text: str) -> bool:
    """Check if extracted text is usable (not empty, not bijoy-garbled)."""
    if not text or len(text.strip()) < 20:
        return False
    if is_bijoy_encoded(text):
        return False
    return True


# ── Text cleaning ────────────────────────────────────────────────────────────────
def clean_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ").replace("\u200b", "").replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if sum(1 for c in stripped if c.isalnum()) >= 2:
            lines.append(stripped)
    return "\n".join(lines).strip()


# ── OCR ─────────────────────────────────────────────────────────────────────────
def ocr_page(page, dpi=OCR_DPI) -> str:
    import fitz
    import pytesseract
    from PIL import Image
    import io

    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img, lang=OCR_LANG)


# ── Per-page extraction ──────────────────────────────────────────────────────────
def extract_page_text(page, use_ocr=True) -> tuple[str, str]:
    """
    Returns (text, method) where method is 'direct', 'ocr', or 'empty'.
    """
    import fitz

    # Try direct text extraction first
    text = page.get_text()

    if is_good_text(text):
        return clean_text(text), "direct"

    # Fall through to OCR
    if use_ocr:
        try:
            ocr_text = ocr_page(page)
            cleaned = clean_text(ocr_text)
            if cleaned:
                return cleaned, "ocr"
        except Exception as e:
            log.debug(f"OCR failed: {e}")

    return "", "empty"


# ── Per-file extraction ──────────────────────────────────────────────────────────
def extract_pdf(pdf_path: Path, use_ocr=True) -> tuple[bool, str]:
    import fitz

    rel  = pdf_path.relative_to(DOWNLOADS_DIR)
    dest = EXTRACTED_DIR / rel.with_suffix(".txt")

    if dest.exists():
        return True, f"SKIP  {pdf_path.name}"

    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        doc = fitz.open(str(pdf_path))
        pages_text = []
        methods = {"direct": 0, "ocr": 0, "empty": 0}

        for i, page in enumerate(doc):
            text, method = extract_page_text(page, use_ocr=use_ocr)
            methods[method] += 1
            if text:
                pages_text.append(f"--- Page {i+1} ---\n{text}")

        doc.close()

        if not pages_text:
            dest.write_text(
                f"[No extractable text — scanned PDF with unrecognizable content]\nSource: {pdf_path}",
                encoding="utf-8"
            )
            return True, f"EMPTY {pdf_path.name}"

        method_summary = ", ".join(f"{v} {k}" for k, v in methods.items() if v > 0)
        full_text = "\n\n".join(pages_text)
        dest.write_text(full_text, encoding="utf-8")

        tag = "OCR  " if methods["ocr"] > 0 and methods["direct"] == 0 else \
              "MIX  " if methods["ocr"] > 0 else "OK   "
        return True, f"{tag} {pdf_path.name} [{method_summary}]"

    except Exception as e:
        log.debug(f"Error on {pdf_path.name}: {e}")
        return False, f"FAIL  {pdf_path.name} — {e}"


# ── Load PDFs ────────────────────────────────────────────────────────────────────
def load_pdfs(dept_filter=None, year_filter=None) -> list:
    pdfs = []
    for pdf in sorted(DOWNLOADS_DIR.rglob("*.pdf")):
        parts = pdf.relative_to(DOWNLOADS_DIR).parts
        dept = parts[0] if len(parts) > 0 else ""
        year = parts[1] if len(parts) > 1 else ""
        if dept_filter and dept.lower() not in dept_filter:
            continue
        if year_filter and year not in year_filter:
            continue
        pdfs.append(pdf)
    return pdfs


# ── Main ─────────────────────────────────────────────────────────────────────────
def run(dept_filter=None, year_filter=None, workers=WORKERS, use_ocr=True):
    check_deps(need_ocr=use_ocr)

    if not DOWNLOADS_DIR.exists():
        log.error("downloads/ folder not found. Run bb_download_pdfs.py first.")
        sys.exit(1)

    pdfs = load_pdfs(dept_filter=dept_filter, year_filter=year_filter)
    pending = [
        p for p in pdfs
        if not (EXTRACTED_DIR / p.relative_to(DOWNLOADS_DIR).with_suffix(".txt")).exists()
    ]

    log.info(f"Total PDFs : {len(pdfs)}")
    log.info(f"Already done: {len(pdfs) - len(pending)}")
    log.info(f"Remaining  : {len(pending)}")
    log.info(f"OCR enabled: {use_ocr} (lang: {OCR_LANG})")

    if not pending:
        log.info("All done!")
        return

    EXTRACTED_DIR.mkdir(exist_ok=True)
    ok = fail = skip = empty = ocr_count = mix_count = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(extract_pdf, p, use_ocr): p for p in pending}
        with tqdm(total=len(futures), desc="Extracting", unit="pdf") as bar:
            for future in as_completed(futures):
                success, msg = future.result()
                if   "SKIP"  in msg: skip      += 1
                elif "EMPTY" in msg: empty     += 1
                elif "OCR"   in msg: ocr_count += 1; ok += 1
                elif "MIX"   in msg: mix_count += 1; ok += 1
                elif success:        ok        += 1
                else:                fail      += 1
                bar.set_postfix(ok=ok, ocr=ocr_count, empty=empty, fail=fail)
                bar.update(1)

    log.info(f"Done.")
    log.info(f"  Direct text : {ok - ocr_count - mix_count}")
    log.info(f"  OCR only    : {ocr_count}")
    log.info(f"  Mixed       : {mix_count}")
    log.info(f"  Empty       : {empty}")
    log.info(f"  Failed      : {fail}")
    log.info(f"  Saved to    : {EXTRACTED_DIR.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract text from BB circular PDFs")
    parser.add_argument("--dept",    nargs="+", help="e.g. brpd fepd")
    parser.add_argument("--year",    nargs="+", help="e.g. 2023 2024")
    parser.add_argument("--workers", type=int, default=WORKERS,
                        help="Parallel workers (keep <=2 for OCR)")
    parser.add_argument("--no-ocr",  action="store_true",
                        help="Skip OCR (fast, text PDFs only)")
    args = parser.parse_args()
    run(
        dept_filter = set(d.lower() for d in args.dept) if args.dept else None,
        year_filter = set(args.year) if args.year else None,
        workers     = args.workers,
        use_ocr     = not args.no_ocr,
    )