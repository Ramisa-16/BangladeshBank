# Bangladesh Bank Circular Scraper & Extractor

A complete pipeline to scrape, download, and extract text from all PDF circulars published on the Bangladesh Bank website (bb.org.bd).

---

## Project Structure

```
bb_scrap/
├── bb_circular_scraper.py     # Step 1: Scrape circular URLs from BB website
├── bb_download_pdfs.py        # Step 2: Download all PDFs via Chrome
├── bb_extract_text.py         # Step 3: Extract text from PDFs (with OCR)
├── output/
│   ├── bb_circulars.csv       # Scraped metadata (url, title, date, dept, circular_no)
│   └── bb_circulars.txt       # Plain list of PDF URLs
├── downloads/                 # Downloaded PDFs organised by dept/year
│   ├── brpd/
│   │   ├── 2024/
│   │   │   ├── may132026brpd-1l17.pdf
│   │   │   └── ...
│   │   └── ...
│   └── ...
├── extracted_text/            # Extracted text files mirroring downloads/ structure
│   ├── brpd/
│   │   ├── 2024/
│   │   │   ├── may132026brpd-1l17.txt
│   │   │   └── ...
│   │   └── ...
│   └── ...
└── chrome_temp/               # Temporary folder used during downloading (auto-cleared)
```

---

## Requirements

### Python Packages
```
pip install selenium undetected-chromedriver requests tqdm pymupdf pytesseract pillow pdfplumber
```

### External Tools
- **Google Chrome** (version 148)
- **Tesseract OCR 5.5+** with Bengali language pack
  - Download: https://github.com/UB-Mannheim/tesseract/wiki
  - During install, select **Bengali** under Additional language data
  - After install, set the path in `bb_extract_text.py`:
    ```python
    pytesseract.pytesseract.tesseract_cmd = r"C:\Users\<YourName>\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
    ```

---

## Usage

### Step 1 — Scrape Circular URLs
Scrapes all circular PDF URLs from the BB website and saves them to `output/bb_circulars.csv`.

```
python bb_circular_scraper.py
```

Options:
```
--no-selenium     Skip Selenium scraping
--no-ajax         Skip AJAX replay attempt
--no-wayback      Skip Wayback Machine fallback
--show-browser    Run Chrome visibly (needed if CAPTCHA appears)
```

**Result:** `output/bb_circulars.csv` with 4,627 circular URLs.

---

### Step 2 — Download PDFs
Downloads each PDF from the CSV using Chrome (required to bypass bot protection).

```
python bb_download_pdfs.py
```

Options:
```
--dept brpd fepd     Download only specific departments
--year 2023 2024     Download only specific years
```

**Notes:**
- Chrome window will open and stay open — do not close it
- Lock your screen (Win+L) safely — downloads continue in background
- Already-downloaded files are skipped on re-run
- Runtime: ~3.5 hours for all 4,627 files

**Result:** 4,600 PDFs downloaded (27 were dead links on BB server).

---

### Step 3 — Extract Text
Extracts and cleans text from all downloaded PDFs. Handles three types:

| PDF Type | Method | Description |
|----------|--------|-------------|
| Unicode text PDF | Direct extraction | Fast, clean output |
| Bijoy-encoded Bangla | OCR | Legacy font detected, re-processed via OCR |
| Scanned/image PDF | OCR | Rasterized and read with Tesseract (ben+eng) |

```
python bb_extract_text.py
```

Options:
```
--dept brpd fepd     Extract only specific departments
--year 2023 2024     Extract only specific years
--workers 2          Parallel workers (keep low for OCR, default: 2)
--no-ocr             Skip OCR, extract text-layer PDFs only (fast)
```

**Notes:**
- Already-extracted files are skipped on re-run
- Runtime: ~1 hour 45 minutes for all 4,600 files with OCR

**Result:**
- Direct text: 1,497 PDFs
- OCR only: 2,727 PDFs
- Mixed (partial text + OCR): 376 PDFs
- Empty: 0 | Failed: 0

---

## Results Summary

| Step | Output | Count |
|------|--------|-------|
| Scrape | Unique circular URLs | 4,627 |
| Download | PDFs successfully downloaded | 4,600 |
| Download | Failed (dead links on BB server) | 27 |
| Extract | Text files successfully extracted | 4,600 |
| Extract | Empty or failed | 0 |

The 27 failed downloads are genuinely broken links on the Bangladesh Bank server — mostly very old circulars from 2002–2006 and a few malformed URLs. They cannot be recovered.

---

## Notes

- The BB website uses TSPD bot protection — direct HTTP requests are blocked. Chrome is required for both scraping and downloading.
- Bijoy-encoded Bangla (legacy SutonnyMJ font) is automatically detected and OCR'd to produce clean Unicode output.
- All text output is UTF-8 encoded.
- Scripts are safe to re-run — they skip already completed files.