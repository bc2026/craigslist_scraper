import re
import csv
import json
import sqlite3
import time
import os
import sys
import html
import requests
from datetime import datetime
from bs4 import BeautifulSoup

SEARCH_URL = "https://poconos.craigslist.org/search/kresgeville-pa/cta?lat=40.9179&lon=-75.5213&max_auto_miles=120000&max_price=10000&search_distance=104"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Disable proxy so requests go direct (fixes 403 when system proxy blocks Craigslist)
PROXIES = {"http": None, "https": None}

# Same DB as web app (web/instance/cars.db)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "web", "instance", "cars.db")


def post_id_from_url(url):
    """Extract Craigslist post ID from listing URL (e.g. .../7918824345.html -> 7918824345)."""
    m = re.search(r"/(\d+)\.html", url)
    return int(m.group(1)) if m else 0


def site_from_url(url):
    """Extract Craigslist site/region from URL (e.g. https://newyork.craigslist.org/... -> newyork)."""
    if not url:
        return ""
    m = re.search(r"https?://([a-z0-9-]+)\.craigslist\.(?:org|com)", url, re.I)
    return (m.group(1).lower() or "") if m else ""


def get_db():
    """Open the shared SQLite DB (same as web app)."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    """Ensure listing table and scraper_state exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS listing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT,
            price TEXT,
            location TEXT,
            mileage TEXT,
            owners TEXT,
            title_status TEXT,
            description TEXT,
            images TEXT,
            reviewed_at TEXT,
            reviewed_by TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_listing_url ON listing(url);
        CREATE TABLE IF NOT EXISTS scraper_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    try:
        conn.execute("SELECT images FROM listing LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE listing ADD COLUMN images TEXT")
    conn.commit()
    conn.close()


def load_state():
    """Load newest post ID we've already listed. If none, use max from existing listings."""
    init_db()
    conn = get_db()
    row = conn.execute(
        "SELECT value FROM scraper_state WHERE key = ?", ("newest_post_id",)
    ).fetchone()
    if row and row[0]:
        conn.close()
        try:
            return int(row[0]) or 0
        except (TypeError, ValueError):
            pass
    # First run or no state: use max post_id from existing listings so we don't re-fetch all
    rows = conn.execute("SELECT url FROM listing").fetchall()
    max_id = max((post_id_from_url(r[0]) for r in rows if r[0]), default=0)
    conn.close()
    return max_id


def save_state(newest_post_id):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO scraper_state (key, value) VALUES (?, ?)",
        ("newest_post_id", str(newest_post_id)),
    )
    conn.commit()
    conn.close()


def upsert_listing(conn, row):
    """Insert or update listing by url. Preserves reviewed_at/reviewed_by on update."""
    url = (row.get("url") or "").strip()
    if not url:
        return
    now = datetime.utcnow().isoformat() + "Z"
    existing = conn.execute("SELECT id, reviewed_at, reviewed_by FROM listing WHERE url = ?", (url,)).fetchone()
    images = row.get("images") or ""
    if isinstance(images, list):
        images = json.dumps(images) if images else ""
    if existing:
        conn.execute(
            """UPDATE listing SET title=?, price=?, location=?, mileage=?, owners=?, title_status=?, description=?, images=?, updated_at=? WHERE url=?""",
            (
                row.get("title", ""),
                row.get("price", ""),
                row.get("location", ""),
                row.get("mileage", ""),
                row.get("owners", ""),
                row.get("title_status", ""),
                row.get("description", ""),
                images,
                now,
                url,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO listing (url, title, price, location, mileage, owners, title_status, description, images, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                url,
                row.get("title", ""),
                row.get("price", ""),
                row.get("location", ""),
                row.get("mileage", ""),
                row.get("owners", ""),
                row.get("title_status", ""),
                row.get("description", ""),
                images,
                now,
                now,
            ),
        )


def get_soup(url):
    r = requests.get(url, headers=HEADERS, timeout=15, proxies=PROXIES)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def parse_detail(url):
    soup = get_soup(url)

    # Title: try several selectors
    title_el = (
        soup.select_one("h1.cl-title")
        or soup.select_one("h1.postingtitle")
        or soup.select_one("[class*='title'] h1")
        or soup.select_one("h1")
    )
    title = title_el.get_text(strip=True) if title_el else ""

    # Price: .price or from title line "$6,500" or in postingtitle
    price_el = soup.select_one(".price") or soup.select_one(".postingtitle .priceinfo")
    price = price_el.get_text(strip=True) if price_el else ""
    if not price and title:
        m = re.search(r"\$[\d,]+", title)
        if m:
            price = m.group(0)

    # Location: only from the link's subdomain (x in x.craigslist.org)
    location = site_from_url(url) or ""

    # Main body / description (avoid using the QR Code block as the description)
    body = soup.select_one("#postingbody")
    desc = ""
    if body:
        full_body_text = body.get_text("\n", strip=True)
        # Strip "QR Code Link to This Post" and similar so we keep the real description
        lines = [
            ln for ln in full_body_text.splitlines()
            if ln.strip()
            and not re.match(r"^QR\s*Code", ln.strip(), re.I)
            and ln.strip() not in ("loading", "reading", "writing", "saving", "searching", "refresh the page.")
        ]
        desc = "\n".join(lines).strip()
        # If nothing left or only trivial, try taking text after "QR Code..." in the raw block
        if not desc or len(desc) < 15:
            after_qr = re.sub(r"^.*?QR\s*Code[^\n]*(?:\n|$)", "", full_body_text, flags=re.I | re.DOTALL).strip()
            if len(after_qr) > len(desc):
                desc = after_qr
    if not body or not desc:
        body = soup.select_one(".postingbody")
        if body:
            raw = body.get_text("\n", strip=True)
            if not re.match(r"^QR\s*Code\s*$", raw.strip(), re.I) or len(raw) > 30:
                desc = raw

    # Fallback: if description is still just QR Code or tiny, take longest paragraph from main content
    if not desc or desc.strip().lower().startswith("qr code") or len(desc.strip()) < 30:
        content_el = soup.select_one("main") or soup.select_one("#content") or soup.find("body")
        if content_el:
            best = ""
            for p in content_el.find_all(["p", "div", "section"]):
                t = p.get_text("\n", strip=True)
                # Skip boilerplate and attribute lines
                if len(t) < 30:
                    continue
                if re.match(r"^(QR\s*Code|post id|posted|loading|reading|writing|saving|searching)", t, re.I):
                    continue
                if re.match(r"^(odometer|title status|condition|fuel|transmission|drive|paint|cylinders|type)\s*:", t, re.I):
                    continue
                if len(t) > len(best):
                    best = t
            if best:
                desc = best

    # Build full content text for attribute regex (attributes often appear above or beside body)
    content_el = soup.select_one("main") or soup.select_one("#content") or soup.find("body")
    full_text = content_el.get_text("\n", strip=True) if content_el else (desc or "")

    # Attributes from p.attrgroup (label: value in spans, sometimes with <b> for value)
    attrs = {}
    for p in soup.select("p.attrgroup"):
        for span in p.select("span"):
            text = span.get_text(" ", strip=True)
            if ":" in text:
                k, v = [t.strip() for t in text.split(":", 1)]
                attrs[k.lower().strip()] = v.strip()
        for span in p.find_all("span", recursive=True):
            raw = span.get_text(strip=True)
            if re.match(r"^(odometer|title status|condition|cylinders|drive|fuel|transmission|paint color|type):", raw, re.I):
                key, _, val = raw.partition(":")
                attrs[key.strip().lower()] = val.strip()

    # Also parse "key: value" lines from full page text (handles different layouts)
    for key in ("odometer", "title status", "condition", "vin"):
        if not attrs.get(key):
            m = re.search(rf"{re.escape(key)}\s*:\s*([^\n]+)", full_text, re.IGNORECASE)
            if m:
                attrs[key] = m.group(1).strip()

    def search(patterns, text=None):
        t = (text or desc or full_text)
        for p in patterns:
            m = re.search(p, t, flags=re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""

    # Mileage: from attrs, then regex (with "k" and "miles" variants)
    mileage = (
        attrs.get("odometer", "")
        or search([r"odometer\s*:\s*([\d,]+)", r"(\d{1,3},?\d{3})\s*(?:miles?|mi)\b", r"(\d+)\s*k\s*miles?", r"(\d+)k\s*mi"], full_text)
    )
    if mileage and re.match(r"^\d+$", mileage.replace(",", "")):
        try:
            n = int(mileage.replace(",", ""))
            mileage = f"{n:,}" if n >= 1000 else str(n)
        except ValueError:
            pass

    title_status = (
        attrs.get("title status", "")
        or search([
            r"title\s*status\s*:\s*(\w+)",
            r"(clean title)",
            r"(rebuilt title)",
            r"(salvage title)",
            r"(rebuilt salvage)",
            r"(clean)",
            r"(salvage)",
        ], full_text)
    )

    owners = search([
        r"(\d+)\s*(?:owner|owners)\b",
        r"(one owner)",
        r"(single owner)",
        r"(\d+)\s*owner",
    ])

    # Image URLs: Craigslist uses data-ids (e.g. "1:abc123,def456") or img[src*="images.craigslist.org"]
    image_urls = []
    for el in soup.select("[data-ids]"):
        raw = el.get("data-ids") or ""
        for part in raw.replace(";", ",").split(","):
            part = part.strip()
            if ":" in part:
                part = part.split(":", 1)[1]
            if part and re.match(r"^[a-zA-Z0-9_-]+$", part):
                image_urls.append(f"https://images.craigslist.org/{part}_600x450.jpg")
    for img in soup.select("img[src*='images.craigslist.org']"):
        src = img.get("src")
        if src and src not in image_urls:
            image_urls.append(src)
    # Deduplicate and cap for CSV
    seen = set()
    unique = []
    for u in image_urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    images_json = json.dumps(unique[:30])

    return {
        "title": title,
        "price": price,
        "location": location,
        "mileage": mileage,
        "owners": owners,
        "title_status": title_status,
        "url": url,
        "description": desc,
        "images": images_json,
    }

def write_html_table(rows, fieldnames, path):
    """Write rows to a readable HTML table file."""
    display_names = {
        "title": "Title",
        "price": "Price",
        "location": "Location",
        "mileage": "Mileage",
        "owners": "Owners",
        "title_status": "Title status",
        "url": "Link",
        "description": "Description",
        "images": "Images",
    }

    def cell(value, is_url=False, is_description=False):
        if value is None:
            value = ""
        s = str(value).strip()
        s = html.escape(s)
        if is_url and s:
            return f'<a href="{html.escape(s)}" target="_blank" rel="noopener">View listing</a>'
        if is_description and len(s) > 300:
            s = s[:297] + "..."
        s = s.replace("\n", "<br>\n")
        return s

    thead = "".join(
        f"<th>{html.escape(display_names.get(f, f.replace('_', ' ').title()))}</th>"
        for f in fieldnames
    )
    trows = []
    for i, r in enumerate(rows):
        tr_class = ' class="even"' if i % 2 == 1 else ""
        tds = []
        for f in fieldnames:
            val = r.get(f, "")
            if f == "url":
                tds.append(f'<td>{cell(val, is_url=True)}</td>')
            elif f == "description":
                tds.append(f'<td class="desc">{cell(val, is_description=True)}</td>')
            elif f == "images":
                try:
                    arr = json.loads(val) if isinstance(val, str) and val.strip() else []
                    val = f"{len(arr)} images" if arr else "—"
                except (TypeError, ValueError):
                    val = "—"
                tds.append(f"<td>{html.escape(str(val))}</td>")
            elif f == "title" and r.get("url"):
                link = html.escape(r["url"])
                title_esc = html.escape(str(r.get("title", "")).strip())
                tds.append(f'<td><a href="{link}" target="_blank" rel="noopener">{title_esc}</a></td>')
            else:
                tds.append(f"<td>{cell(val)}</td>")
        trows.append(f"<tr{tr_class}>" + "".join(tds) + "</tr>")

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Craigslist cars – {len(rows)} listings</title>
<style>
  body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 1rem 2rem; background: #f5f5f5; }}
  h1 {{ color: #333; }}
  .wrap {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.1); min-width: 800px; }}
  th {{ background: #333; color: #fff; text-align: left; padding: 0.6rem 0.75rem; font-weight: 600; }}
  td {{ padding: 0.5rem 0.75rem; border-bottom: 1px solid #eee; vertical-align: top; }}
  tr.even {{ background: #fafafa; }}
  tr:hover {{ background: #f0f7ff; }}
  td.desc {{ max-width: 320px; font-size: 0.9em; color: #444; line-height: 1.4; }}
  a {{ color: #0066cc; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>Craigslist cars &amp; trucks</h1>
<p>{len(rows)} listings. Generated from search.</p>
<div class="wrap">
<table>
<thead><tr>{thead}</tr></thead>
<tbody>
""" + "\n".join(trows) + """
</tbody>
</table>
</div>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_content)


def main():
    search_soup = get_soup(SEARCH_URL)

    # Debug: see what we got
    print("Page length:", len(search_soup.text))
    result_rows = search_soup.select("li.cl-static-search-result, li.result-row")
    print("Found result rows:", len(result_rows))

    def is_listing_url(href):
        if not href or len(href) < 10:
            return False
        # Relative path like /cto/d/city-title/123.html or full URL
        return "/cto/" in href or "/ctd/" in href or "/cta/" in href or re.search(r"\d+\.html", href) is not None

    links = []
    for li in result_rows:
        # Try explicit selectors first, then any <a> that looks like a listing link
        a = li.select_one("a.cl-app-anchor.text-only") or li.select_one("a.cl-app-anchor") or li.select_one("a.hdrlnk")
        if not a or not a.get("href"):
            for tag in li.select("a[href]"):
                href = tag.get("href") or ""
                if is_listing_url(href):
                    a = tag
                    break
        if a and a.get("href"):
            href = a["href"]
            if href.startswith("/"):
                href = "https://poconos.craigslist.org" + href
            if is_listing_url(href):
                links.append(href)

    # Only process listings newer than the newest we've already listed
    cutoff = load_state()
    links = [url for url in links if post_id_from_url(url) > cutoff]
    print("Newest already listed (post ID):", cutoff)
    print("Links to fetch (newer only):", len(links))

    if not links:
        print("No new listings since last run. Exiting.")
        return

    init_db()
    conn = get_db()
    inserted = updated = 0
    max_id = cutoff
    for i, url in enumerate(links, start=1):
        print(f"Scraping {i}/{len(links)}: {url}")
        try:
            row = parse_detail(url)
            status = (row.get("title_status") or "").strip().lower()
            if status and "clean" in status:
                before = conn.execute("SELECT 1 FROM listing WHERE url = ?", ((row.get("url") or "").strip(),)).fetchone()
                upsert_listing(conn, row)
                if before:
                    updated += 1
                else:
                    inserted += 1
                max_id = max(max_id, post_id_from_url(row.get("url", "")))
                print("  Got row:", row["title"][:50] if row["title"] else "(no title)", "|", row["price"])
            else:
                print("  Skipped (not clean title):", row["title"][:40] if row.get("title") else url)
            time.sleep(1.5)  # be gentle to Craigslist
        except Exception as e:
            print("Error on", url, e)
    conn.commit()
    conn.close()
    save_state(max_id)
    print(f"DB: {inserted} new, {updated} updated. Newest post ID: {max_id}")


def db_listings_for_export():
    """Return (rows, fieldnames) from DB for export/HTML. Rows are dicts with listing fields."""
    init_db()
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT url, title, price, location, mileage, owners, title_status, description, images FROM listing ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    fieldnames = ["title", "price", "location", "mileage", "owners", "title_status", "url", "description", "images"]
    return [dict((k, (r[k] if r[k] is not None else "")) for k in fieldnames) for r in rows], fieldnames


def csv_to_html():
    """Read listings from DB and write HTML table (no scraping)."""
    rows, fieldnames = db_listings_for_export()
    if not rows:
        print("No listings in DB. Run the scraper first.")
        return
    html_path = os.path.join(SCRIPT_DIR, "craigslist_cars_detailed.html")
    write_html_table(rows, fieldnames, html_path)
    print(f"Wrote HTML table to {html_path} ({len(rows)} rows)")


def refresh_locations():
    """Recompute location from each listing's URL subdomain (x in x.craigslist.org). No scraping."""
    init_db()
    conn = get_db()
    rows = conn.execute("SELECT id, url FROM listing").fetchall()
    n = 0
    for (lid, url) in rows:
        if url:
            loc = site_from_url(url) or ""
            conn.execute("UPDATE listing SET location = ?, updated_at = ? WHERE id = ?", (loc, datetime.utcnow().isoformat() + "Z", lid))
            n += 1
    conn.commit()
    conn.close()
    print(f"Updated location for {n} listings in DB.")


def export_csv_html():
    """Write CSV and HTML files from current DB (for backup or external use)."""
    rows, fieldnames = db_listings_for_export()
    if not rows:
        print("No listings in DB. Run the scraper first.")
        return
    csv_path = os.path.join(SCRIPT_DIR, "craigslist_cars_detailed.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {csv_path} ({len(rows)} rows)")
    html_path = os.path.join(SCRIPT_DIR, "craigslist_cars_detailed.html")
    write_html_table(rows, fieldnames, html_path)
    print(f"Wrote {html_path}")


def run_watch(interval_minutes=60):
    """Run scraper in a loop; writes to DB. Ctrl+C to stop."""
    try:
        interval_sec = max(1, int(interval_minutes) * 60)
    except (TypeError, ValueError):
        interval_sec = 3600
    print(f"Watch mode: scraping every {interval_sec // 60} minutes. Ctrl+C to stop.")
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"Run failed: {e}. Retrying in {interval_sec // 60} min.")
        print(f"Sleeping {interval_sec // 60} minutes until next run...")
        time.sleep(interval_sec)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        main()
    else:
        arg = sys.argv[1].lower()
        if arg in ("html", "--html", "-h", "--html-only"):
            csv_to_html()
        elif arg in ("watch", "--watch", "-w"):
            mins = int(sys.argv[2]) if len(sys.argv) > 2 else 60
            run_watch(mins)
        elif arg in ("refresh-locations", "locations", "--locations"):
            refresh_locations()
        elif arg in ("export", "--export", "-e"):
            export_csv_html()
        else:
            main()
