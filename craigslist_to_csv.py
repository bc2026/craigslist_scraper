import re
import csv
import json
import time
import os
import sys
import html
import requests
from bs4 import BeautifulSoup

SEARCH_URL = "https://poconos.craigslist.org/search/kresgeville-pa/cta?lat=40.9179&lon=-75.5213&max_auto_miles=120000&max_price=10000&search_distance=104"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Disable proxy so requests go direct (fixes 403 when system proxy blocks Craigslist)
PROXIES = {"http": None, "https": None}

STATE_FILENAME = "craigslist_scraper_state.json"


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


def location_label_from_site(site):
    """Human-readable location from site subdomain (e.g. newyork -> New York, poconos -> Poconos)."""
    if not site:
        return ""
    # Title-case and fix multi-word (newyork -> New York, lasvegas -> Las Vegas)
    known = {
        "newyork": "New York",
        "losangeles": "Los Angeles",
        "sanfrancisco": "San Francisco",
        "sanjose": "San Jose",
        "washingtondc": "Washington DC",
        "lasvegas": "Las Vegas",
        "newjersey": "New Jersey",
        "hudsonvalley": "Hudson Valley",
        "longisland": "Long Island",
        "southjersey": "South Jersey",
        "cnj": "Central NJ",
        "jerseyshore": "Jersey Shore",
        "poconos": "Poconos",
        "harrisburg": "Harrisburg",
        "philadelphia": "Philadelphia",
        "allentown": "Allentown",
        "york": "York",
        "reading": "Reading",
        "scranton": "Scranton",
        "pennstate": "Penn State",
        "delaware": "Delaware",
    }
    return known.get(site.lower(), site.replace("-", " ").title())


def load_state():
    """Load newest post ID we've already listed (so we only fetch newer)."""
    out_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(out_dir, STATE_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return int(data.get("newest_post_id", 0)) or 0
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def save_state(newest_post_id):
    out_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(out_dir, STATE_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"newest_post_id": newest_post_id}, f)


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

    # Location: .location or from title " - City, ST" or .postingtitle
    hood_el = (
        soup.select_one(".location")
        or soup.select_one(".postingtitle .postinglocation")
        or soup.select_one("[class*='location']")
    )
    location = hood_el.get_text(strip=True) if hood_el else ""
    if not location and title and " - " in title:
        parts = title.split(" - ", 2)
        if len(parts) >= 2:
            location = parts[-1].strip()
    # Fallback: get location from URL (e.g. newyork.craigslist.org -> "New York")
    site = site_from_url(url)
    if not location and site:
        location = location_label_from_site(site)
    elif location and site:
        # Prepend site/region so we always know which Craigslist (e.g. "New York · Fresh Meadows")
        site_label = location_label_from_site(site)
        if site_label and site_label not in location:
            location = f"{site_label} · {location}"

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
    out_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(out_dir, "craigslist_cars_detailed.csv")
    if os.path.isfile(csv_path):
        try:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    u = (row.get("url") or "").strip()
                    if u:
                        pid = post_id_from_url(u)
                        if pid > cutoff:
                            cutoff = pid
        except Exception:
            pass
    links = [url for url in links if post_id_from_url(url) > cutoff]
    print("Newest already listed (post ID):", cutoff)
    print("Links to fetch (newer only):", len(links))

    if not links:
        print("No new listings since last run. Exiting.")
        return

    rows = []
    for i, url in enumerate(links, start=1):
        print(f"Scraping {i}/{len(links)}: {url}")
        try:
            row = parse_detail(url)
            status = (row.get("title_status") or "").strip().lower()
            if status and "clean" in status:
                rows.append(row)
                print("  Got row:", row["title"][:50] if row["title"] else "(no title)", "|", row["price"])
            else:
                print("  Skipped (not clean title):", row["title"][:40] if row.get("title") else url)
            time.sleep(1.5)  # be gentle to Craigslist
        except Exception as e:
            print("Error on", url, e)

    fieldnames = [
        "title",
        "price",
        "location",
        "mileage",
        "owners",
        "title_status",
        "url",
        "description",
        "images",
    ]

    # Merge with existing CSV so we keep old listings and only add/update new ones
    by_url = {}
    if os.path.isfile(csv_path):
        try:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    u = (row.get("url") or "").strip()
                    if u:
                        by_url[u] = {k: row.get(k, "") for k in fieldnames if k in row}
        except Exception:
            pass
    for r in rows:
        u = (r.get("url") or "").strip()
        if u:
            by_url[u] = {k: r.get(k, "") for k in fieldnames}
    merged = list(by_url.values())
    merged.sort(key=lambda r: post_id_from_url(r.get("url", "")), reverse=True)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in merged:
            w.writerow(r)
    print(f"Wrote {len(merged)} rows to {csv_path} ({len(rows)} new this run)")

    max_id = max((post_id_from_url(r.get("url", "")) for r in merged), default=0)
    save_state(max_id)

    html_path = os.path.join(out_dir, "craigslist_cars_detailed.html")
    write_html_table(merged, fieldnames, html_path)
    print(f"Wrote HTML table to {html_path}")

    # Optional: push CSV to family review site so it auto-updates
    sync_url = os.environ.get("SYNC_URL") or os.environ.get("CRAIGSLIST_SYNC_URL")
    if sync_url:
        sync_url = sync_url.rstrip("/")
        if not sync_url.endswith("/api/sync"):
            sync_url = sync_url + "/api/sync"
        try:
            with open(csv_path, "rb") as f:
                r = requests.post(sync_url, files={"file": ("craigslist_cars_detailed.csv", f, "text/csv")}, timeout=30, proxies=PROXIES)
            r.raise_for_status()
            print("Synced to site:", r.json().get("inserted", 0), "new,", r.json().get("updated", 0), "updated")
        except Exception as e:
            print("Sync failed:", e)


def csv_to_html():
    """Read existing CSV and write HTML table (no scraping)."""
    out_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(out_dir, "craigslist_cars_detailed.csv")
    html_path = os.path.join(out_dir, "craigslist_cars_detailed.html")
    if not os.path.isfile(csv_path):
        print(f"CSV not found: {csv_path}. Run the scraper first.")
        return
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or (list(rows[0].keys()) if rows else [])
    write_html_table(rows, fieldnames, html_path)
    print(f"Wrote HTML table to {html_path} ({len(rows)} rows)")


def run_watch(interval_minutes=60):
    """Run scraper in a loop so CSV and HTML stay updated. Ctrl+C to stop."""
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
        else:
            main()
