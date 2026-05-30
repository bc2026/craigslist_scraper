#!/usr/bin/env python3
"""
Facebook Marketplace car scraper.

Setup (one time):
    pip install playwright
    playwright install chromium
    python facebook_scraper.py login   # opens browser, log in manually, saves cookies

Usage:
    python facebook_scraper.py          # single scrape run
    python facebook_scraper.py watch 60 # repeat every 60 minutes
"""

import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

# ── Config ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "web" / "instance" / "cars.db"
COOKIES_PATH = SCRIPT_DIR / "fb_cookies.json"

# Edit these to match your search area and budget
SEARCH_URL = (
    "https://www.facebook.com/marketplace/category/vehicles"
    "?minPrice=500&maxPrice=10000&maxMileage=120000"
    "&latitude=40.9179&longitude=-75.5213&radius=100"
    "&vehicleType=car_truck&daysSinceListed=7"
    "&sortBy=creation_time_descend"
)

REQUEST_DELAY = 3.5   # seconds between detail page requests
SCROLL_ROUNDS = 6     # how many times to scroll to load more results
MAX_LISTINGS = 60     # cap per scrape run


# ── Database ─────────────────────────────────────────────────────────────────
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_source_column(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(listing)").fetchall()]
    if "source" not in cols:
        conn.execute("ALTER TABLE listing ADD COLUMN source TEXT DEFAULT 'craigslist'")
        conn.execute(
            "UPDATE listing SET source='craigslist' WHERE source IS NULL OR source=''"
        )
        conn.commit()


def upsert_listing(conn, row):
    now = datetime.now(timezone.utc).isoformat()
    url = row["url"]
    existing = conn.execute(
        "SELECT id FROM listing WHERE url=?", (url,)
    ).fetchone()
    images = json.dumps(row.get("images") or [])
    if existing:
        conn.execute(
            """UPDATE listing SET title=?, price=?, location=?, mileage=?,
               description=?, images=?, source='facebook', updated_at=?
               WHERE url=?""",
            (
                row.get("title"), row.get("price"), row.get("location"),
                row.get("mileage"), row.get("description"), images, now, url,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO listing
               (url, title, price, location, mileage, description, images, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,'facebook',?,?)""",
            (
                url, row.get("title"), row.get("price"), row.get("location"),
                row.get("mileage"), row.get("description"), images, now, now,
            ),
        )
    conn.commit()


# ── Cookie helpers ────────────────────────────────────────────────────────────
def load_cookies(context):
    if COOKIES_PATH.exists():
        with open(COOKIES_PATH) as f:
            context.add_cookies(json.load(f))


def save_cookies(context):
    with open(COOKIES_PATH, "w") as f:
        json.dump(context.cookies(), f, indent=2)


# ── Interactive login (run once) ──────────────────────────────────────────────
def do_login():
    print("Opening browser — log in to Facebook manually, then press Enter here.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.facebook.com/login")
        input("Press Enter after you have logged in completely... ")
        save_cookies(context)
        browser.close()
    print(f"Cookies saved → {COOKIES_PATH}")


# ── Parsing helpers ───────────────────────────────────────────────────────────
def extract_mileage(text):
    if not text:
        return None
    for pat, multiplier in [
        (r"(\d[\d,]+)\s*(?:miles?|mi)\b", 1),
        (r"(\d+)k\s*(?:miles?|mi)\b", 1000),
        (r"odometer[:\s]+(\d[\d,]+)", 1),
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = int(m.group(1).replace(",", "")) * multiplier
            return f"{val:,} mi"
    return None


def dismiss_modal(page):
    for sel in [
        '[aria-label="Close"]',
        '[data-testid="close-button"]',
        'div[role="dialog"] [aria-label="Close"]',
    ]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                time.sleep(0.4)
                return
        except Exception:
            pass


# ── Parse one listing detail page ─────────────────────────────────────────────
def parse_detail(page, url):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
        dismiss_modal(page)

        # Title
        title = None
        for sel in ["h1", '[data-testid="marketplace-pdp-title"]', "h1.x1heor9g"]:
            el = page.query_selector(sel)
            if el:
                t = el.inner_text().strip()
                if t and len(t) > 2:
                    title = t
                    break

        # Price — look for $ amount
        price = None
        for sel in ["h2", "h3", '[class*="price"]']:
            els = page.query_selector_all(sel)
            for el in els:
                t = el.inner_text().strip()
                if re.search(r"\$\s*\d", t):
                    price = t.split("\n")[0].strip()
                    break
            if price:
                break

        # Description
        desc = ""
        for sel in [
            'div[style*="white-space: pre-wrap"]',
            '[data-testid="marketplace-listing-item-description"]',
            'div[class*="x1iorvi4"]',
        ]:
            el = page.query_selector(sel)
            if el:
                t = el.inner_text().strip()
                if len(t) > 20:
                    desc = t
                    break

        # Location
        location = None
        for sel in [
            '[data-testid="marketplace-pdp-location"]',
            'span[class*="x193iq5w"]',
        ]:
            el = page.query_selector(sel)
            if el:
                t = el.inner_text().strip()
                if t and not t.startswith("$"):
                    location = t
                    break

        # Images (scontent CDN URLs)
        imgs = []
        seen = set()
        for img in page.query_selector_all("img[src]"):
            src = img.get_attribute("src") or ""
            if "scontent" in src and src not in seen and "emoji" not in src:
                seen.add(src)
                imgs.append(src)

        mileage = extract_mileage(desc)

        return {
            "url": url,
            "title": title,
            "price": price,
            "location": location,
            "mileage": mileage,
            "description": desc[:1500],
            "images": imgs[:20],
        }
    except PlaywrightTimeout:
        print(f"  timeout: {url}")
        return None
    except Exception as e:
        print(f"  error on {url}: {e}")
        return None


# ── Collect listing URLs from search results page ─────────────────────────────
def collect_urls(page, search_url):
    print("Loading FB Marketplace search...")
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightTimeout:
        print("Search page timed out, continuing with what loaded")
    time.sleep(3)
    dismiss_modal(page)

    for i in range(SCROLL_ROUNDS):
        page.keyboard.press("End")
        time.sleep(1.8)

    urls = set()
    for a in page.query_selector_all("a[href]"):
        href = a.get_attribute("href") or ""
        if "/marketplace/item/" in href:
            clean = href.split("?")[0].rstrip("/")
            if not clean.startswith("http"):
                clean = "https://www.facebook.com" + clean
            urls.add(clean)

    return list(urls)[:MAX_LISTINGS]


# ── Single scrape pass ────────────────────────────────────────────────────────
def run_once():
    if not COOKIES_PATH.exists():
        print("No saved session found.")
        print("Run first: python facebook_scraper.py login")
        sys.exit(1)

    conn = get_conn()
    ensure_source_column(conn)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        load_cookies(context)
        page = context.new_page()

        # Verify session is still active
        page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=20000)
        time.sleep(2)
        if "login" in page.url or page.query_selector('input[name="email"]'):
            print("Facebook session expired. Run: python facebook_scraper.py login")
            browser.close()
            conn.close()
            sys.exit(1)

        urls = collect_urls(page, SEARCH_URL)
        print(f"Found {len(urls)} listings to process")

        saved = 0
        for i, url in enumerate(urls, 1):
            print(f"  [{i}/{len(urls)}] ", end="", flush=True)
            row = parse_detail(page, url)
            if row and row.get("title"):
                upsert_listing(conn, row)
                saved += 1
                print(row["title"][:60])
            else:
                print("(skipped)")
            time.sleep(REQUEST_DELAY)

        save_cookies(context)
        browser.close()

    conn.close()
    print(f"\nDone — {saved} FB Marketplace listings saved")


# ── Watch mode ────────────────────────────────────────────────────────────────
def run_watch(interval_minutes=60):
    print(f"Watch mode — scraping every {interval_minutes} min. Ctrl-C to stop.")
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"Run failed: {e}")
        print(f"Sleeping {interval_minutes} min...")
        time.sleep(interval_minutes * 60)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "login":
        do_login()
    elif cmd == "watch":
        mins = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        run_watch(mins)
    else:
        run_once()
