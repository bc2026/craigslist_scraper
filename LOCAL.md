# Test locally

## 1. Start the review site

```bash
cd craigslist_scraper/web
pip install -r requirements.txt
python app.py
```

(Use `PORT=5001 python app.py` if port 5000 is in use.)  
Then open **http://127.0.0.1:5000** (or 5001) in your browser. You’ll see the “Enter your name” landing page.

## 2. Data: shared DB

The scraper and the web app use the **same SQLite DB** (`web/instance/cars.db`). Run the scraper (or `watch`) in another terminal; new listings appear in the DB and the site shows them immediately. No CSV or sync step.

Optional: import from a CSV once via `POST /api/sync` (e.g. `curl -X POST -F "file=@craigslist_cars_detailed.csv" http://127.0.0.1:5000/api/sync`).

## 3. Use the app

1. Open http://127.0.0.1:5000 (or your PORT).
2. Enter your name and click **Continue**.
3. You should see the list of cars once the scraper has run and written to the DB (or after a one-time CSV import).
4. Click **View & review** on a listing, add a review, and/or **Mark as reviewed**.
