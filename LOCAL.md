# Test locally

## 1. Start the review site

```bash
cd craigslist_scraper/web
pip install -r requirements.txt
python app.py
```

(Use `PORT=5001 python app.py` if port 5000 is in use.)  
Then open **http://127.0.0.1:5000** (or 5001) in your browser. You’ll see the “Enter your name” landing page.

## 2. Data: automatic sync

The site **automatically** watches the scraper’s CSV and re-imports when it changes.

- It looks for **`craigslist_cars_detailed.csv`** in the **project root** (parent of `web/`).
- Every **2 minutes** it checks that file; if it’s newer than the last sync, it imports it. No manual `curl` needed.

So: run the scraper (or `watch`) in another terminal; when it writes the CSV, the site will pick it up within a couple of minutes. To load immediately the first time, either run the scraper once so the file exists, or do a one-time manual sync:

```bash
curl -X POST -F "file=@craigslist_cars_detailed.csv" http://127.0.0.1:5000/api/sync
```

Optional env vars for the **web** app:

- `AUTO_SYNC=0` – turn off automatic sync.
- `AUTO_SYNC_INTERVAL_SECONDS=60` – check every 60 seconds (default 120).
- `CRAIGSLIST_CSV=/path/to/file.csv` – use a different CSV path.

## 3. Use the app

1. Open http://127.0.0.1:5000 (or your PORT).
2. Enter your name and click **Continue**.
3. You should see the list of cars once the CSV exists and has been synced (automatically or via one-time `curl`).
4. Click **View & review** on a listing, add a review, and/or **Mark as reviewed**.
