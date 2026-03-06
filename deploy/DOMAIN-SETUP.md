# Serve the app at cars.bhag.dev

You need to: (1) point **cars.bhag.dev** to your EC2 IP in DNS, and (2) put a reverse proxy on EC2 so the app is reachable on port 80/443 instead of :5000.

---

## 1. DNS (where you manage bhag.dev)

Add a record for the subdomain **cars**:

| Type | Name  | Value           | TTL  |
|------|--------|-----------------|------|
| **A**  | `cars` | `YOUR_EC2_IP`   | 300  |

Replace `YOUR_EC2_IP` with your instance’s public IP (e.g. `100.52.170.72`).

- **Name**: `cars` (so the hostname is **cars.bhag.dev**). Some providers want `cars.bhag.dev` in the name field; use what they require.
- If your provider only supports CNAME: use **CNAME** `cars` → your EC2 public hostname (e.g. `ec2-100-52-170-72.compute-1.amazonaws.com`). An A record is simpler if available.

Wait a few minutes, then check:

```bash
dig cars.bhag.dev +short
# or
nslookup cars.bhag.dev
```

You should see your EC2 IP.

---

## 2. Security group (AWS)

Allow HTTP and HTTPS to the instance:

- **Inbound rule 1**: Type **HTTP**, Port **80**, Source **0.0.0.0/0**
- **Inbound rule 2**: Type **HTTPS**, Port **443**, Source **0.0.0.0/0**

Save. No need to restart the instance.

---

## 3. Reverse proxy on EC2 (nginx)

**Option A – run the script (easiest)**

SSH into the instance, then from the project:

```bash
cd ~/craigslist_scraper
chmod +x deploy/setup-nginx-domain.sh
sudo ./deploy/setup-nginx-domain.sh
```

That installs nginx, adds a vhost for **cars.bhag.dev** → port 5000, and reloads nginx. For HTTPS as well:

```bash
sudo ./deploy/setup-nginx-domain.sh --https
```

(Certbot will prompt for an email. For a different domain, pass it first: `sudo ./deploy/setup-nginx-domain.sh other.example.com --https`.)

**Option B – manual steps**

Install nginx, create `/etc/nginx/sites-available/cars.bhag.dev` with a `server { listen 80; server_name cars.bhag.dev; location / { proxy_pass http://127.0.0.1:5000; ... } }` block, enable the site, then `sudo nginx -t` and `sudo systemctl reload nginx`.

**Test**

Open **http://cars.bhag.dev** in your browser. You should see the “Enter your name” page.

---

## 4. HTTPS (optional, Let’s Encrypt)

**Install Certbot**

```bash
sudo apt-get install -y certbot python3-certbot-nginx
```

**Get certificate (nginx must already be serving cars.bhag.dev on port 80)**

```bash
sudo certbot --nginx -d cars.bhag.dev
```

Follow the prompts (email, agree to terms). Certbot will adjust nginx for HTTPS and set up renewal.

Then use **https://cars.bhag.dev**. Renewal is automatic.

**Access by IP:** After Certbot, requests to **http://YOUR_EC2_IP/** may get nginx 404 because only the redirect block listens on 80. To fix, add a **default** server block at the **top** of `/etc/nginx/sites-available/cars.bhag.dev` so the IP serves the app:

```nginx
server {
    listen 80 default_server;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Then `sudo nginx -t && sudo systemctl reload nginx`. After that, http://YOUR_EC2_IP/ works; cars.bhag.dev still redirects to HTTPS.

---

The scraper and web app share the same DB (`web/instance/cars.db`); no sync URL or CSV step is needed.

---

## Summary

| Step | What to do |
|------|------------|
| DNS | A record `cars` → EC2 public IP |
| Security group | Allow inbound 80, 443 |
| EC2 | Install nginx, config for `cars.bhag.dev` → proxy to 127.0.0.1:5000 |
| Optional | Certbot for https://cars.bhag.dev |

After that, the app is at **http://cars.bhag.dev** (and **https://cars.bhag.dev** if you did step 4).
