#!/usr/bin/env python3
"""
Bangladesh Bank Circular PDF Scraper v2 (date-filter fix)
==========================================================
- Pins ChromeDriver to version 148
- Sets date range 2000-01-01 → today before scraping
- Falls back to JS injection if date inputs not found by CSS
"""

import csv, logging, re, time, sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Dependency check ────────────────────────────────────────────────────────────
def check_deps():
    missing = []
    for mod, pkg in [("selenium","selenium"),("undetected_chromedriver","undetected-chromedriver"),
                     ("requests","requests"),("tqdm","tqdm")]:
        try: __import__(mod)
        except ImportError: missing.append(pkg)
    if missing:
        print(f"[ERROR] Missing packages: {', '.join(missing)}")
        print(f"  Run: pip install {' '.join(missing)}")
        sys.exit(1)

check_deps()

import requests
from tqdm import tqdm

SELENIUM_AVAILABLE = False
SELENIUM_ERROR = ""
try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait, Select
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
    SELENIUM_AVAILABLE = True
except Exception as e:
    SELENIUM_ERROR = str(e)

# ── Config ──────────────────────────────────────────────────────────────────────
BASE_URL      = "https://www.bb.org.bd/en/index.php/mediaroom/circular"
AJAX_URL      = "https://www.bb.org.bd/en/index.php/mediaroom/getcirculars"
OUTPUT_DIR    = Path("output")
OUTPUT_CSV    = OUTPUT_DIR / "bb_circulars.csv"
OUTPUT_TXT    = OUTPUT_DIR / "bb_circulars.txt"
PAGE_SIZE     = 100
REQUEST_DELAY = 2.0
HEADLESS      = True
FROM_DATE     = "2000-01-01"
TO_DATE       = datetime.today().strftime("%Y-%m-%d")
CHROME_VER    = 148   # pin to your installed Chrome version

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────────
def normalize_url(href: str) -> str:
    href = href.strip()
    if href.startswith("//"): return "https:" + href
    if href.startswith("/"): return "https://www.bb.org.bd" + href
    return href

def extract_pdfs_from_html(html: str) -> list:
    return [normalize_url(m) for m in re.findall(r'href=["\']([^"\']*\.pdf[^"\']*)["\']', html, re.I)]

def enrich_from_url(record: dict) -> dict:
    url = record.get("url", "")
    m = re.search(r'/circulars/([a-z]+)/([a-z]{3}\d{2}\d{4})([a-z]+)(\d+)([be])\.pdf', url, re.I)
    if m:
        _, date_part, dept_code, num, lang = m.groups()
        if not record.get("department"): record["department"] = dept_code.upper()
        if not record.get("circular_no"): record["circular_no"] = num
        if not record.get("date"):
            try: record["date"] = datetime.strptime(date_part, "%b%d%Y").strftime("%Y-%m-%d")
            except: pass
        if not record.get("title"):
            record["title"] = f"{dept_code.upper()} Circular No. {num} ({'English' if lang.lower()=='e' else 'Bengali'})"
    return record

def dedupe(records: list) -> list:
    seen, out = set(), []
    for r in records:
        u = r.get("url", "").strip()
        if u and u not in seen:
            seen.add(u); out.append(r)
    return out

def save_results(records: list):
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["url","title","date","department","circular_no"])
        w.writeheader(); w.writerows(records)
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(r["url"] for r in records))
    log.info(f"Saved {len(records)} records -> {OUTPUT_CSV}")


# ── Strategy 1: Selenium ────────────────────────────────────────────────────────
class SeleniumScraper:
    def __init__(self, headless=HEADLESS):
        self.headless = headless
        self.driver = None
        self.session_cookies = {}

    def _start(self):
        log.info("Starting Chrome via undetected-chromedriver...")
        opts = uc.ChromeOptions()
        if self.headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--disable-gpu")
        opts.add_experimental_option("prefs", {
            "profile.managed_default_content_settings.images": 2
        })
        try:
            self.driver = uc.Chrome(options=opts, use_subprocess=True, version_main=CHROME_VER)
            self.driver.set_page_load_timeout(60)
            log.info("Chrome started successfully")
        except WebDriverException as e:
            raise RuntimeError(
                f"\nChrome failed to start! Error: {e}\n"
                f"Fix: winget install Google.Chrome  or  winget upgrade Google.Chrome\n"
            )

    def _stop(self):
        if self.driver:
            try: self.driver.quit()
            except: pass

    def _harvest_cookies(self):
        self.session_cookies = {c["name"]: c["value"] for c in self.driver.get_cookies()}

    def _set_page_length(self, length=100):
        try:
            sel = Select(self.driver.find_element(By.CSS_SELECTOR, "select[name$='_length']"))
            sel.select_by_value(str(length))
            time.sleep(2.5)
            log.info(f"Set page length to {length}")
        except NoSuchElementException:
            log.warning("Could not set page length")

    def _set_date_range(self, from_date: str, to_date: str):
        """Set From/To date filters and reload the table to show all entries."""
        log.info(f"Setting date range: {from_date} to {to_date}")

        # First: log ALL inputs on the page so we know exact names
        try:
            inputs = self.driver.find_elements(By.CSS_SELECTOR, "input, select")
            log.info(f"Page has {len(inputs)} input/select elements:")
            for el in inputs:
                log.info(f"  tag={el.tag_name} name={el.get_attribute('name')!r} "
                         f"id={el.get_attribute('id')!r} type={el.get_attribute('type')!r} "
                         f"value={el.get_attribute('value')!r}")
        except Exception as e:
            log.warning(f"Could not list inputs: {e}")

        # Strategy A: find inputs by common name/id patterns
        set_ok = False
        for from_pat, to_pat in [
            ('input[name="from_date"]',   'input[name="to_date"]'),
            ('input[name="fromDate"]',     'input[name="toDate"]'),
            ('input[name="start_date"]',   'input[name="end_date"]'),
            ('input#from_date',            'input#to_date'),
            ('input#fromDate',             'input#toDate'),
            ('input[id*="from"]',          'input[id*="to"]'),
        ]:
            try:
                f_el = self.driver.find_elements(By.CSS_SELECTOR, from_pat)
                t_el = self.driver.find_elements(By.CSS_SELECTOR, to_pat)
                if f_el and t_el:
                    self.driver.execute_script("arguments[0].value = '';", f_el[0])
                    f_el[0].send_keys(from_date)
                    time.sleep(0.3)
                    self.driver.execute_script("arguments[0].value = '';", t_el[0])
                    t_el[0].send_keys(to_date)
                    time.sleep(0.3)
                    log.info(f"Date fields found: {from_pat} / {to_pat}")
                    set_ok = True
                    break
            except Exception as e:
                log.debug(f"Pattern {from_pat}: {e}")

        if not set_ok:
            # Strategy B: JS scan of all inputs for from/to keywords
            log.info("Trying JS-based date injection...")
            self.driver.execute_script(f"""
                document.querySelectorAll('input').forEach(function(el) {{
                    var key = (el.name + ' ' + el.id + ' ' + el.placeholder).toLowerCase();
                    if (key.match(/from|start|begin/)) {{
                        el.value = '{from_date}';
                        el.dispatchEvent(new Event('input', {{bubbles:true}}));
                        el.dispatchEvent(new Event('change', {{bubbles:true}}));
                    }}
                    if (key.match(/\\bto\\b|end|finish/)) {{
                        el.value = '{to_date}';
                        el.dispatchEvent(new Event('input', {{bubbles:true}}));
                        el.dispatchEvent(new Event('change', {{bubbles:true}}));
                    }}
                }});
            """)
            time.sleep(1)

        # Click submit / search button
        for btn_sel in [
            'button[type="submit"]', 'input[type="submit"]',
            '.btn-search', '.search-btn', 'button.btn-primary',
            'button:contains("Search")', 'button:contains("Filter")',
        ]:
            try:
                btns = self.driver.find_elements(By.CSS_SELECTOR, btn_sel)
                if btns:
                    btns[0].click()
                    log.info(f"Clicked submit: {btn_sel}")
                    time.sleep(4)
                    break
            except Exception:
                pass
        else:
            # No submit button found - try pressing Enter
            try:
                active = self.driver.switch_to.active_element
                active.send_keys(Keys.RETURN)
                time.sleep(4)
            except Exception:
                pass

    def _get_total_pages(self) -> int:
        try:
            info = self.driver.find_element(By.CSS_SELECTOR, "[id$='_info']").text
            m = re.search(r'of\s+([\d,]+)\s+entries', info)
            if m:
                total = int(m.group(1).replace(",", ""))
                pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
                log.info(f"DataTables: {total:,} total entries -> {pages} pages")
                return pages
        except Exception as e:
            log.warning(f"Could not read total: {e}")
        return 1

    def _parse_table(self) -> list:
        records = []
        rows = []
        for sel in ["table#circular_list tbody tr", "table.dataTable tbody tr", "table tbody tr"]:
            rows = self.driver.find_elements(By.CSS_SELECTOR, sel)
            if rows: break
        for row in rows:
            try:
                cells = row.find_elements(By.TAG_NAME, "td")
                if not cells: continue
                links = row.find_elements(By.CSS_SELECTOR, "a[href*='.pdf']")
                if not links: continue
                url   = normalize_url(links[0].get_attribute("href") or "")
                title = cells[1].text.strip() if len(cells) > 1 else ""
                date  = cells[2].text.strip() if len(cells) > 2 else ""
                dept  = cells[0].text.strip() if cells else ""
                circ_no = ""
                m = re.search(r'(?:no\.?|#)\s*(\d+)', title, re.I)
                if m: circ_no = m.group(1)
                if url:
                    records.append({"url":url,"title":title,"date":date,
                                    "department":dept,"circular_no":circ_no})
            except Exception: pass
        return records

    def _click_next(self) -> bool:
        for sel in [
            "a.paginate_button.next:not(.disabled)",
            "button.paginate_button.next:not(.disabled)",
            "#circular_list_next:not(.disabled)",
            "[id$='_next']:not(.disabled)",
        ]:
            btns = self.driver.find_elements(By.CSS_SELECTOR, sel)
            if btns:
                self.driver.execute_script("arguments[0].click();", btns[0])
                time.sleep(REQUEST_DELAY)
                return True
        return False

    def run(self) -> list:
        log.info("── Strategy 1: Selenium ────────────────────────────────────")
        self._start()
        all_records, seen = [], set()
        try:
            log.info(f"Loading {BASE_URL}")
            self.driver.get(BASE_URL)
            time.sleep(6)

            page_src = self.driver.page_source.lower()
            if "captcha" in page_src or ("human" in page_src and "visitor" in page_src):
                if self.headless:
                    log.warning("CAPTCHA detected! Run with --show-browser to solve manually.")
                    self._stop(); return []
                else:
                    log.info("CAPTCHA detected - please solve it. Waiting 60s...")
                    time.sleep(60)

            log.info("Waiting for DataTable...")
            try:
                WebDriverWait(self.driver, 30).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "table.dataTable,table#circular_list,.dataTables_wrapper")
                    )
                )
                log.info("DataTable found!")
            except TimeoutException:
                log.error("DataTable not found. Page title: " + self.driver.title)
                self._stop(); return []

            # ── KEY FIX: set date range BEFORE reading page count ──
            self._set_date_range(FROM_DATE, TO_DATE)
            time.sleep(3)

            # Set rows per page AFTER date filter (table reloads)
            self._set_page_length(100)
            time.sleep(3)

            total_pages = self._get_total_pages()

            # Page 1
            records = self._parse_table()
            for r in records:
                if r["url"] not in seen:
                    seen.add(r["url"]); all_records.append(r)
            log.info(f"Page 1/{total_pages}: {len(records)} rows, running total: {len(all_records)}")
            self._harvest_cookies()

            # Pages 2+
            for page in tqdm(range(2, total_pages + 1), desc="Scraping pages", unit="page"):
                if not self._click_next():
                    log.info(f"Last page reached at {page-1}")
                    break
                records = self._parse_table()
                new = 0
                for r in records:
                    if r["url"] not in seen:
                        seen.add(r["url"]); all_records.append(r); new += 1
                log.info(f"Page {page}/{total_pages}: +{new} new (total: {len(all_records)})")
                self._harvest_cookies()

        except Exception as e:
            log.error(f"Selenium error: {e}", exc_info=True)
        finally:
            self._stop()

        log.info(f"Strategy 1 done: {len(all_records)} records")
        return all_records


# ── Strategy 2: AJAX Replay ─────────────────────────────────────────────────────
class AjaxScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36"),
            "X-Requested-With": "XMLHttpRequest",
            "Referer": BASE_URL,
            "Accept": "application/json, text/javascript, */*; q=0.01",
        })

    def _fetch(self, start: int, draw: int = 1) -> Optional[dict]:
        params = {"draw":draw,"start":start,"length":PAGE_SIZE,
                  "search[value]":"","search[regex]":"false",
                  "from_date":FROM_DATE,"to_date":TO_DATE}
        for url in [AJAX_URL, BASE_URL]:
            for method in ("POST", "GET"):
                try:
                    r = (self.session.post(url, data=params, timeout=30)
                         if method == "POST"
                         else self.session.get(url, params=params, timeout=30))
                    if r.status_code == 200:
                        try:
                            data = r.json()
                            if "data" in data or "aaData" in data:
                                return data
                        except Exception:
                            pdfs = extract_pdfs_from_html(r.text)
                            if pdfs:
                                return {"data":[{"url":p} for p in pdfs], "recordsTotal":len(pdfs)}
                except Exception as e:
                    log.debug(f"  {method} {url}: {e}")
        return None

    def _parse_row(self, row) -> dict:
        rec = {"url":"","title":"","date":"","department":"","circular_no":""}
        items = row if isinstance(row, list) else list(row.values())
        for cell in items:
            cell_str = str(cell)
            if not rec["url"]:
                m = re.search(r'href=["\']([^"\']*\.pdf[^"\']*)["\']', cell_str, re.I)
                if m: rec["url"] = normalize_url(m.group(1))
            tm = re.search(r'>([^<]{10,})</a>', cell_str)
            if tm and not rec["title"]: rec["title"] = tm.group(1).strip()
        return rec

    def run(self, seed_cookies: dict = None) -> list:
        log.info("── Strategy 2: AJAX Replay ──────────────────────────────────")
        if seed_cookies:
            log.info(f"Injecting {len(seed_cookies)} Selenium cookies")
            for k, v in seed_cookies.items():
                self.session.cookies.set(k, v, domain="www.bb.org.bd")
        else:
            try:
                r = self.session.get(BASE_URL, timeout=30)
                log.info(f"Cold warm-up: {r.status_code}")
            except Exception as e:
                log.warning(f"Warm-up failed: {e}")

        first = self._fetch(start=0, draw=1)
        if not first:
            log.error("AJAX blocked – TSPD requires real browser session cookies")
            return []

        total = int(first.get("recordsTotal") or first.get("iTotalRecords") or 0)
        rows  = first.get("data") or first.get("aaData") or []
        log.info(f"AJAX: {total:,} total records reported")

        all_records, seen = [], set()
        for row in rows:
            r = self._parse_row(row)
            if r["url"] and r["url"] not in seen:
                seen.add(r["url"]); all_records.append(r)

        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        for page in tqdm(range(1, pages), desc="AJAX pages", unit="page"):
            time.sleep(REQUEST_DELAY)
            data = self._fetch(start=page * PAGE_SIZE, draw=page + 1)
            if not data: continue
            for row in (data.get("data") or data.get("aaData") or []):
                r = self._parse_row(row)
                if r["url"] and r["url"] not in seen:
                    seen.add(r["url"]); all_records.append(r)

        log.info(f"Strategy 2 done: {len(all_records)} records")
        return all_records


# ── Strategy 3: Wayback Machine ─────────────────────────────────────────────────
class WaybackScraper:
    CDX = "https://web.archive.org/cdx/search/cdx"

    def run(self) -> list:
        log.info("── Strategy 3: Wayback Machine CDX ─────────────────────────")
        params = {"url":"www.bb.org.bd/mediaroom/circulars/*.pdf",
                  "output":"json","fl":"original,timestamp",
                  "filter":"mimetype:application/pdf",
                  "collapse":"original","limit":50000}
        try:
            r = requests.get(self.CDX, params=params, timeout=120)
            if r.status_code != 200:
                log.warning(f"CDX returned {r.status_code}")
                return []
            data = r.json()
            if len(data) < 2: return []
            header = data[0]
            records, seen = [], set()
            for row in data[1:]:
                d = dict(zip(header, row))
                url = d.get("original", "")
                if url and url not in seen and ".pdf" in url.lower():
                    seen.add(url)
                    ts = d.get("timestamp", "")
                    date = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else ""
                    records.append({"url":url,"title":"","date":date,
                                    "department":"","circular_no":""})
            log.info(f"Wayback: {len(records)} archived URLs found")
            return records
        except Exception as e:
            log.error(f"Wayback CDX error: {e}")
            return []


# ── Main ────────────────────────────────────────────────────────────────────────
def run(use_selenium=True, use_ajax=True, use_wayback=True, headless=HEADLESS):
    OUTPUT_DIR.mkdir(exist_ok=True)
    all_records = []
    sel_cookies = {}

    if use_selenium:
        if not SELENIUM_AVAILABLE:
            log.error(f"Selenium import error: {SELENIUM_ERROR}")
            log.error("Fix: pip install selenium undetected-chromedriver --upgrade")
        else:
            s = SeleniumScraper(headless=headless)
            recs = s.run()
            sel_cookies = s.session_cookies
            all_records.extend(recs)
            log.info(f"After Selenium: {len(dedupe(all_records))} unique URLs")

    if use_ajax:
        recs = AjaxScraper().run(seed_cookies=sel_cookies or None)
        all_records.extend(recs)
        log.info(f"After AJAX: {len(dedupe(all_records))} unique URLs")

    if use_wayback:
        recs = WaybackScraper().run()
        all_records.extend(recs)
        log.info(f"After Wayback: {len(dedupe(all_records))} unique URLs")

    all_records = dedupe(all_records)
    all_records = [enrich_from_url(r) for r in all_records]
    all_records.sort(key=lambda r: r.get("date", "") or "", reverse=True)
    save_results(all_records)
    log.info(f"FINAL: {len(all_records)} unique PDF circulars")
    log.info(f"  CSV -> {OUTPUT_CSV.resolve()}")
    log.info(f"  TXT -> {OUTPUT_TXT.resolve()}")
    return all_records


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Bangladesh Bank Circular PDF Scraper v2")
    p.add_argument("--no-selenium",  action="store_true", help="Skip Selenium")
    p.add_argument("--no-ajax",      action="store_true", help="Skip AJAX replay")
    p.add_argument("--no-wayback",   action="store_true", help="Skip Wayback Machine")
    p.add_argument("--show-browser", action="store_true", help="Non-headless Chrome")
    args = p.parse_args()
    run(
        use_selenium = not args.no_selenium,
        use_ajax     = not args.no_ajax,
        use_wayback  = not args.no_wayback,
        headless     = not args.show_browser,
    )