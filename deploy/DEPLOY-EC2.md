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

## 5. How to know the deployment succeeded

**On the EC2 instance (SSH session):**

1. **Setup script finished without errors**  
   You should have seen “Done. Web: http://…” at the end. Any Python or “command not found” errors mean a step failed.

2. **Check that both services are running:**
   ```bash
   sudo systemctl status craigslist-web craigslist-scraper
   ```
   Both should say **active (running)** in green. If either says **failed** or **inactive**, the deploy didn’t fully succeed.

3. **Optional – test the web app from the instance:**
   ```bash
   curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/
   ```
   You should see **200**. Anything else (e.g. 000, 502) means the app isn’t responding.

**From your Mac (browser):**

4. **Open the site** (use your instance’s public IP instead of 100.52.170.72 if different):
   ```text
   http://YOUR_EC2_PUBLIC_IP:5000
   ```
   You should see the **“Enter your name”** landing page. If the page never loads, check the security group allows **TCP port 5000** from your IP (or 0.0.0.0/0).

**Scraper:**

5. The scraper runs every 60 minutes. To confirm it’s working:
   ```bash
   sudo journalctl -u craigslist-scraper -n 30 --no-pager
   ```
   After at least one run you should see lines like “Scraping 1/…”, “Wrote … rows”, and possibly “Synced to site”.

If all of the above are true, the instructions succeeded.

---

## 6. Open the site

In your browser:

**http://YOUR_EC2_PUBLIC_IP:5000**

You should see the “Enter your name” landing page. The scraper runs in the background and writes new listings to the same DB as the site every 60 minutes.

---

## 7. Redeploy (after code changes)

From your **Mac**, copy the updated project to EC2 (use your key and instance IP/user):

```bash
cd /Users/bc
scp -i /path/to/your-key.pem -r craigslist_scraper ubuntu@YOUR_EC2_IP:~/
```

Then **SSH in** and restart both services so they use the new code:

```bash
sudo systemctl restart craigslist-web craigslist-scraper
```

No need to run `setup-ec2.sh` again unless you changed dependencies or systemd unit files.

---

## 8. Deploy from git (update on new commits)

To pull the latest code on EC2 instead of copying with `scp`, use git.

**Prerequisite:** Your code is in a git repo you can pull from (e.g. GitHub, GitLab). The repo root should contain `craigslist_to_csv.py` and the `web/` folder.

### One-time: switch EC2 to git

If you already deployed with `scp`, you can turn the app dir into a git clone:

**On EC2 (SSH):**

```bash
# Backup DB and scraper state if you care about them
cp ~/craigslist_scraper/web/instance/cars.db ~/ 2>/dev/null || true

# Replace app dir with a clone (use your repo URL)
rm -rf ~/craigslist_scraper
git clone https://github.com/YOUR_USER/craigslist_scraper.git ~/craigslist_scraper

# Restore DB if you backed it up
cp ~/cars.db ~/craigslist_scraper/web/instance/ 2>/dev/null || true

# Re-run setup so venv and systemd point at the clone
cd ~/craigslist_scraper && sudo ./deploy/setup-ec2.sh
```

Use your real repo URL. If the repo is private, use a personal access token or deploy key.

### Option A: Deploy after each push (one command from Mac)

After you `git push`, run (replace key path and host):

```bash
ssh -i /path/to/your-key.pem ubuntu@YOUR_EC2_IP 'cd ~/craigslist_scraper && git pull && sudo systemctl restart craigslist-web craigslist-scraper'
```

### Option B: EC2 pulls on a schedule (cron)

On EC2, pull every 10 minutes and restart only if something changed:

```bash
sudo tee /usr/local/bin/craigslist-deploy << 'EOF'
#!/bin/bash
cd /home/ubuntu/craigslist_scraper || exit 1
before=$(git rev-parse HEAD 2>/dev/null)
git pull --ff-only -q 2>/dev/null || true
after=$(git rev-parse HEAD 2>/dev/null)
if [ -n "$after" ] && [ "$before" != "$after" ]; then
  sudo systemctl restart craigslist-web craigslist-scraper
  echo "Deployed $after"
fi
EOF
sudo chmod +x /usr/local/bin/craigslist-deploy
(crontab -l 2>/dev/null; echo "*/10 * * * * /usr/local/bin/craigslist-deploy") | crontab -
```

(Use `ec2-user` instead of `ubuntu` on Amazon Linux. If `sudo systemctl` asks for a password in cron, run `sudo crontab -e` and add the same line there so it runs as root.)

### Option C: Deploy on push with GitHub Actions

On push to `main`, GitHub Actions can SSH to EC2 and run `git pull` + restart. Add a deploy key or SSH key to the repo secrets and create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to EC2
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.EC2_HOST }}
          username: ubuntu
          key: ${{ secrets.EC2_SSH_KEY }}
          script: |
            cd ~/craigslist_scraper && git pull --ff-only && sudo systemctl restart craigslist-web craigslist-scraper
```

In the repo **Settings → Secrets and variables → Actions** add `EC2_HOST` (e.g. `3.214.184.4`) and `EC2_SSH_KEY` (full private key content). The EC2 key pair’s **public** key must be in `~/.ssh/authorized_keys` on the instance.

---

## 9. Useful commands (on EC2)

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

- **Scraper not updating the site**  
  Scraper and web app share `web/instance/cars.db`. Ensure both services use the same app directory so they see the same DB file.
