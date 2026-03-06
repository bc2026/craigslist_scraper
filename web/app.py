"""
Family car review site: view Craigslist listings, add reviews, mark as reviewed.
Listings are in the same SQLite DB the scraper writes to. Run: flask --app app run
Optional: POST /api/sync with a CSV to import listings manually.
"""
import csv
import io
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    request,
    send_from_directory,
)

# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "cars.db"


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
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
        CREATE TABLE IF NOT EXISTS review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL REFERENCES listing(id),
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            stars INTEGER CHECK (stars >= 1 AND stars <= 5),
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_listing_url ON listing(url);
        CREATE INDEX IF NOT EXISTS idx_review_listing ON review(listing_id);
    """)
    # Migration: add stars column if missing (existing DBs)
    try:
        conn.execute("SELECT stars FROM review LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE review ADD COLUMN stars INTEGER")
    # Migration: add images column to listing if missing
    try:
        conn.execute("SELECT images FROM listing LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE listing ADD COLUMN images TEXT")
    conn.commit()
    conn.close()


def listing_row_to_dict(row):
    d = dict(row)
    d["reviewed"] = bool(d.get("reviewed_at"))
    return d


def sync_from_csv_content(raw):
    """Import listings from CSV string. Returns (inserted, updated)."""
    reader = csv.DictReader(io.StringIO(raw))
    fieldnames = reader.fieldnames or []
    if "url" not in fieldnames:
        return None
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_db()
    inserted = updated = 0
    for row in reader:
        url = (row.get("url") or "").strip()
        if not url:
            continue
        existing = conn.execute("SELECT id FROM listing WHERE url = ?", (url,)).fetchone()
        images = (row.get("images") or "").strip()
        if existing:
            conn.execute(
                """UPDATE listing SET title=?, price=?, location=?, mileage=?, owners=?, title_status=?, description=?, images=?, updated_at=?
                   WHERE url = ?""",
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
            updated += 1
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
            inserted += 1
    conn.commit()
    conn.close()
    return (inserted, updated)


# -----------------------------------------------------------------------------
# Flask app
# -----------------------------------------------------------------------------
app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB for CSV upload


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/listings", methods=["GET"])
def api_listings():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, url, title, price, location, mileage, owners, title_status, description, images, reviewed_at, reviewed_by, created_at, updated_at FROM listing ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return jsonify([listing_row_to_dict(r) for r in rows])


@app.route("/api/listings/<int:listing_id>", methods=["GET"])
def api_listing(listing_id):
    conn = get_db()
    row = conn.execute(
        "SELECT id, url, title, price, location, mileage, owners, title_status, description, images, reviewed_at, reviewed_by, created_at, updated_at FROM listing WHERE id = ?",
        (listing_id,),
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    reviews = conn.execute(
        "SELECT id, listing_id, author, body, stars, created_at FROM review WHERE listing_id = ? ORDER BY created_at ASC",
        (listing_id,),
    ).fetchall()
    conn.close()
    out = listing_row_to_dict(row)
    out["reviews"] = [dict(r) for r in reviews]
    return jsonify(out)


@app.route("/api/listings/<int:listing_id>/reviews", methods=["POST"])
def api_add_review(listing_id):
    data = request.get_json() or {}
    author = (data.get("author") or "").strip()
    body = (data.get("body") or "").strip()
    stars = data.get("stars")
    if not author or not body:
        return jsonify({"error": "author and body required"}), 400
    if stars is not None:
        try:
            stars = int(stars)
            if stars < 1 or stars > 5:
                stars = None
        except (TypeError, ValueError):
            stars = None
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_db()
    conn.execute(
        "INSERT INTO review (listing_id, author, body, stars, created_at) VALUES (?, ?, ?, ?, ?)",
        (listing_id, author, body, stars, now),
    )
    # Automatically mark listing as reviewed when first review is added
    conn.execute(
        "UPDATE listing SET reviewed_at = ?, reviewed_by = ?, updated_at = ? WHERE id = ?",
        (now, author, now, listing_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/listings/<int:listing_id>/reviewed", methods=["POST"])
def api_mark_reviewed(listing_id):
    data = request.get_json() or {}
    reviewed_by = (data.get("reviewed_by") or "Family").strip() or "Family"
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_db()
    cur = conn.execute(
        "UPDATE listing SET reviewed_at = ?, reviewed_by = ?, updated_at = ? WHERE id = ?",
        (now, reviewed_by, now, listing_id),
    )
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True, "reviewed_at": now})


@app.route("/api/sync", methods=["POST"])
def api_sync():
    """Import listings from uploaded CSV (same columns as craigslist_cars_detailed.csv)."""
    if "file" not in request.files and not request.get_data():
        return jsonify({"error": "Send CSV as form field 'file' or raw body"}), 400
    if "file" in request.files:
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "No file selected"}), 400
        raw = f.read().decode("utf-8", errors="replace")
    else:
        raw = request.get_data().decode("utf-8", errors="replace")
    result = sync_from_csv_content(raw)
    if result is None:
        return jsonify({"error": "CSV must have 'url' column"}), 400
    inserted, updated = result
    return jsonify({"ok": True, "inserted": inserted, "updated": updated})


# Ensure DB exists when app is loaded (e.g. by gunicorn)
init_db()

# -----------------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
