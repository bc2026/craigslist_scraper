#!/bin/bash
# Run on a fresh Amazon Linux 2023 or Ubuntu 22.04 EC2 instance.
# Usage: sudo ./setup-ec2.sh
# Scraper and web app share web/instance/cars.db; no sync config needed.

set -e
APP_USER="${APP_USER:-ubuntu}"
APP_DIR="${APP_DIR:-/home/$APP_USER/craigslist_scraper}"
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing system packages..."
if command -v dnf &>/dev/null; then
    sudo dnf install -y python3 python3-pip
elif command -v apt-get &>/dev/null; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-venv
else
    echo "Unsupported OS. Install Python 3 and pip manually."
    exit 1
fi

echo "Creating app directory and venv..."
sudo mkdir -p "$APP_DIR"
sudo chown "$APP_USER:$APP_USER" "$APP_DIR"

sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$DEPLOY_DIR/../requirements.txt"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$DEPLOY_DIR/../web/requirements.txt"

REPO_ROOT="${REPO_ROOT:-$(cd "$DEPLOY_DIR/.." && pwd)}"
# Only copy if repo is in a different location (e.g. when running from a build host)
# When you scp the whole project to EC2, REPO_ROOT and APP_DIR are the same, so skip copy.
REPO_ABS="$(cd "$REPO_ROOT" && pwd)"
APP_ABS="$(cd "$APP_DIR" 2>/dev/null && pwd)" || true
if [ -n "$APP_ABS" ] && [ "$REPO_ABS" = "$APP_ABS" ]; then
    echo "App already at $APP_DIR, skipping copy."
else
    echo "Copying app files from $REPO_ROOT to $APP_DIR..."
    sudo -u "$APP_USER" cp -r "$REPO_ROOT/craigslist_to_csv.py" "$REPO_ROOT/web" "$APP_DIR/"
fi
sudo -u "$APP_USER" mkdir -p "$APP_DIR/web/static" "$APP_DIR/web/instance"

echo "Installing systemd services..."
sed -e "s|/home/ubuntu|/home/$APP_USER|g" -e "s|ubuntu|$APP_USER|g" \
    -e "s|/home/ubuntu/craigslist_scraper|$APP_DIR|g" \
    "$DEPLOY_DIR/craigslist-scraper.service" | sudo tee /etc/systemd/system/craigslist-scraper.service > /dev/null
sed -e "s|/home/ubuntu|/home/$APP_USER|g" -e "s|ubuntu|$APP_USER|g" \
    -e "s|/home/ubuntu/craigslist_scraper|$APP_DIR|g" \
    "$DEPLOY_DIR/craigslist-web.service" | sudo tee /etc/systemd/system/craigslist-web.service > /dev/null

# Point services at APP_DIR and use venv
sudo sed -i "s|WorkingDirectory=.*|WorkingDirectory=$APP_DIR|" /etc/systemd/system/craigslist-scraper.service
sudo sed -i "s|ExecStart=.*|ExecStart=$APP_DIR/venv/bin/python3 $APP_DIR/craigslist_to_csv.py watch 60|" /etc/systemd/system/craigslist-scraper.service
sudo sed -i "s|WorkingDirectory=.*|WorkingDirectory=$APP_DIR/web|" /etc/systemd/system/craigslist-web.service
sudo sed -i "s|ExecStart=.*|ExecStart=$APP_DIR/venv/bin/gunicorn --bind 0.0.0.0:5000 --workers 2 --access-logfile - --error-logfile - app:app|" /etc/systemd/system/craigslist-web.service

sudo systemctl daemon-reload
sudo systemctl enable craigslist-web craigslist-scraper
sudo systemctl start craigslist-web
sudo systemctl start craigslist-scraper

echo "Done. Web: http://$(curl -s -S http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo 'YOUR_EC2_IP'):5000"
echo "Check: sudo systemctl status craigslist-web craigslist-scraper"
echo "Logs:  sudo journalctl -u craigslist-scraper -f"
