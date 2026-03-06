# Deploy to your EC2 instance (bhag)

**Instance:** `i-04bbab577f8d7cee2`  
**Public IP:** `100.52.170.72`  
**Site URL after deploy:** http://100.52.170.72:5000

---

## 1. Open port 5000 on the instance

In AWS Console:

1. Go to **EC2 → Instances**, select your instance.
2. Open the **Security** tab → click the **Security group** link.
3. **Edit inbound rules** → **Add rule**:
   - Type: **Custom TCP**
   - Port: **5000**
   - Source: **Anywhere (0.0.0.0/0)** or “My IP” if you want to restrict access.
4. Save.

---

## 2. SSH into the instance

From your Mac (use the key you used when launching the instance):

```bash
# If your key is named something like bhag.pem or key.pem:
ssh -i /path/to/your-key.pem ec2-user@100.52.170.72
```

- **Amazon Linux 2023 / Amazon Linux 2:** user is `ec2-user`
- **Ubuntu:** user is `ubuntu`

If you’re not sure, try `ec2-user` first; if it fails, try `ubuntu`.

---

## 3. Copy the project to EC2

From your **Mac** (in a new terminal), from the folder that **contains** `craigslist_scraper`:

```bash
cd /Users/bc
scp -i /path/to/your-key.pem -r craigslist_scraper ec2-user@100.52.170.72:~/
```

Use `ubuntu@...` if your instance is Ubuntu.

---

## 4. Run the setup script on EC2

Back in your **SSH session** on the instance:

```bash
cd ~/craigslist_scraper
chmod +x deploy/setup-ec2.sh
sudo ./deploy/setup-ec2.sh
```

If your SSH user is **ubuntu**, the script will use `ubuntu` and `/home/ubuntu/craigslist_scraper` by default.  
If you logged in as **ec2-user**, run:

```bash
sudo APP_USER=ec2-user ./deploy/setup-ec2.sh
```

The script will:

- Install Python 3 and pip (if needed)
- Create a venv and install dependencies
- Install two systemd services: **craigslist-web** (site) and **craigslist-scraper** (scraper every 60 min)
- Start both services

---

## 5. Open the site

In your browser:

**http://100.52.170.72:5000**

You should see the “Enter your name” landing page. The scraper runs in the background and will sync new listings to the site about every 60 minutes.

---

## 6. Useful commands (on EC2)

```bash
# Check both services
sudo systemctl status craigslist-web craigslist-scraper

# Scraper logs (live)
sudo journalctl -u craigslist-scraper -f

# Web logs
sudo journalctl -u craigslist-web -f

# Restart after you change code or config
sudo systemctl restart craigslist-web
sudo systemctl restart craigslist-scraper
```

---

## If something fails

- **“Address already in use”**  
  Something is using port 5000. Stop it or change the app port (e.g. in the web service use `--bind 0.0.0.0:5001` and open 5001 in the security group).

- **Site not loading**  
  Confirm the security group allows inbound TCP 5000 from your IP (or 0.0.0.0/0). Confirm the web service is running: `sudo systemctl status craigslist-web`.

- **Scraper not syncing**  
  It posts to `http://127.0.0.1:5000` by default. If the web app is on another port or host, set `SYNC_URL` for the scraper (see main deploy README).
