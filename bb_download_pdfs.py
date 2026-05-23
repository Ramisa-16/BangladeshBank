#!/usr/bin/env python3
"""
Bangladesh Bank Circular PDF Downloader
Uses visible Chrome to download each PDF (headless breaks downloads).

Usage:
    python bb_download_pdfs.py
    python bb_download_pdfs.py --dept brpd
    python bb_download_pdfs.py --year 2023 2024
"""

import csv
import logging
import re
import sys
import time
from pathlib import Path

import undetected_chromedriver as uc
from tqdm import tqdm

DOWNLOADS_DIR = Path("downloads")
CSV_PATH      = Path("output/bb_circulars.csv")
TEMP_DIR      = Path("chrome_temp")
DELAY         = 2.0
CHROME_VER    = 148

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def parse_url(url: str) -> tuple:
    m = re.search(r'/circulars/([^/]+)/([^/]+\.pdf)', url, re.I)
    if m:
        dept = m.group(1).lower()
        ym = re.search(r'(20\d{2})', m.group(2))
        year = ym.group(1) if ym else "0000"
        return dept, year
    return "unknown", "0000"


def make_dest(url: str) -> Path:
    dept, year = parse_url(url)
    raw = url.rstrip("/").split("/")[-1]
    fname = re.sub(r'[^\w\-_.]', '_', raw)
    return DOWNLOADS_DIR / dept / year / fname


def load_urls(csv_path: Path, dept_filter=None, year_filter=None) -> list:
    urls = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = row.get("url", "").strip()
            if not url or not url.lower().endswith(".pdf"):
                continue
            dept, year = parse_url(url)
            if dept_filter and dept not in dept_filter:
                continue
            if year_filter and year not in year_filter:
                continue
            urls.append(url)
    return urls


def wait_for_download(temp_dir: Path, timeout: int = 30) -> Path | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pdfs = [f for f in temp_dir.glob("*.pdf")]
        if pdfs:
            return pdfs[0]
        time.sleep(0.5)
    return None


def start_driver() -> uc.Chrome:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    opts = uc.ChromeOptions()
    # NO headless — headless breaks PDF downloads
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=800,600")
    opts.add_experimental_option("prefs", {
        "download.default_directory": str(TEMP_DIR.resolve()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
    })
    driver = uc.Chrome(options=opts, use_subprocess=True, version_main=CHROME_VER)
    driver.get("https://www.bb.org.bd/en/index.php/mediaroom/circular")
    time.sleep(6)
    log.info("Chrome session established")
    return driver


def run(dept_filter=None, year_filter=None):
    if not CSV_PATH.exists():
        log.error(f"CSV not found: {CSV_PATH}")
        sys.exit(1)

    urls = load_urls(CSV_PATH, dept_filter=dept_filter, year_filter=year_filter)
    pending = [u for u in urls if not make_dest(u).exists()]
    log.info(f"Total: {len(urls)} | Already done: {len(urls)-len(pending)} | Remaining: {len(pending)}")

    if not pending:
        log.info("All done!")
        return

    driver = start_driver()
    ok = fail = 0

    try:
        with tqdm(total=len(pending), desc="Downloading", unit="pdf") as bar:
            for i, url in enumerate(pending):
                dest = make_dest(url)
                if dest.exists():
                    bar.update(1)
                    continue

                # Clear temp dir
                for f in TEMP_DIR.glob("*"):
                    try: f.unlink()
                    except: pass

                try:
                    driver.get(url)
                    downloaded = wait_for_download(TEMP_DIR, timeout=30)

                    if downloaded:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        downloaded.rename(dest)
                        ok += 1
                    else:
                        log.debug(f"Timeout: {url}")
                        fail += 1

                except Exception as e:
                    log.debug(f"Error on {url}: {e}")
                    fail += 1
                    try: driver.quit()
                    except: pass
                    time.sleep(3)
                    driver = start_driver()

                bar.set_postfix(ok=ok, fail=fail)
                bar.update(1)
                time.sleep(DELAY)

                # Restart Chrome every 500 downloads
                if (i + 1) % 500 == 0:
                    log.info("Restarting Chrome to keep session fresh...")
                    try: driver.quit()
                    except: pass
                    time.sleep(3)
                    driver = start_driver()

    finally:
        try: driver.quit()
        except: pass

    log.info(f"Done.  Downloaded={ok}  Failed={fail}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", nargs="+", help="e.g. brpd fepd")
    parser.add_argument("--year", nargs="+", help="e.g. 2023 2024")
    args = parser.parse_args()
    run(
        dept_filter=set(d.lower() for d in args.dept) if args.dept else None,
        year_filter=set(args.year) if args.year else None,
    )