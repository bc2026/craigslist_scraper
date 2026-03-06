# EC2 deployment: scraper + family review site

Runs the Craigslist scraper **always** in the background and the review site so the family can view and review cars. Scraper and web app share one SQLite DB; new listings appear on the site as soon as the scraper writes them.

## What runs

| Service | What it does |
|--------|----------------|
| **craigslist-web** | Flask app (Gunicorn) on port 5000. Family opens `http://YOUR_EC2_IP:5000`. |
| **craigslist-scraper** | Scraper in watch mode (every 60 min). Writes new listings to `web/instance/cars.db` (same DB as the site). |

Both are systemd services: they start on boot and restart if they crash.

## Quick setup on EC2

1. **Launch an instance**  
   Amazon Linux 2023 or Ubuntu 22.04, t3.micro is enough. Open port **5000** in the security group (HTTP or custom TCP 5000).

2. **Copy the project to the instance**  
   From your machine (replace `YOUR_EC2_IP` and key):
   ```bash
   scp -i your-key.pem -r /path/to/craigslist_scraper ubuntu@YOUR_EC2_IP:~/
   ```

3. **On the EC2 instance**, from the repo root:
   ```bash
   cd ~/craigslist_scraper
   chmod +x deploy/setup-ec2.sh
   sudo ./deploy/setup-ec2.sh
   ```

4. **Open the site**  
   In a browser: `http://YOUR_EC2_IP:5000`

## Optional: change user or directory

```bash
sudo APP_USER=ec2-user APP_DIR=/opt/craigslist_scraper ./deploy/setup-ec2.sh
```

Then copy the app into `APP_DIR` before running (or set `REPO_ROOT` if the script is run from elsewhere).

Scraper and web app use the same DB file (`web/instance/cars.db`); no sync URL or CSV step is needed.

## Useful commands

```bash
# Status
sudo systemctl status craigslist-web craigslist-scraper

# Logs (scraper)
sudo journalctl -u craigslist-scraper -f

# Logs (web)
sudo journalctl -u craigslist-web -f

# Restart after code/config change
sudo systemctl restart craigslist-web
sudo systemctl restart craigslist-scraper
```

## Manual install (no setup script)

1. Install Python 3 and pip.
2. Create a venv, install deps:
   - Repo root: `pip install -r requirements.txt`
   - `web/`: `pip install -r web/requirements.txt`
3. Copy `deploy/craigslist-scraper.service` and `deploy/craigslist-web.service` to `/etc/systemd/system/`.
4. Edit both: set `WorkingDirectory`, `User`, `ExecStart` to your paths and venv.
5. Run:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now craigslist-web craigslist-scraper
   ```
