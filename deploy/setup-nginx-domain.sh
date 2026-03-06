#!/bin/bash
# Run on EC2 (Ubuntu/Debian) to put nginx in front of the app and optionally get HTTPS.
# Usage:
#   sudo ./setup-nginx-domain.sh                    # HTTP only, domain cars.bhag.dev
#   sudo ./setup-nginx-domain.sh --https            # HTTP + HTTPS (Let's Encrypt)
#   sudo ./setup-nginx-domain.sh subdomain.example.com --https
#
# Requires: DNS for the domain already points to this server's public IP.

set -e
DOMAIN="${1:-cars.bhag.dev}"
BACKEND_PORT="${BACKEND_PORT:-5000}"

# If first arg is --https, do HTTPS after nginx; domain is then second arg or default
DO_HTTPS=false
if [ "$1" = "--https" ] || [ "$1" = "-s" ]; then
    DO_HTTPS=true
    DOMAIN="${2:-cars.bhag.dev}"
fi

echo "Domain: $DOMAIN"
echo "Backend: http://127.0.0.1:$BACKEND_PORT"
echo "HTTPS:   $DO_HTTPS"
echo ""

# Install nginx (Debian/Ubuntu)
if ! command -v nginx &>/dev/null; then
    echo "Installing nginx..."
    apt-get update
    apt-get install -y nginx
else
    echo "Nginx already installed."
fi

# Remove default site so our vhost handles port 80
rm -f /etc/nginx/sites-enabled/default

# Write vhost (default_server so requests by IP also get the app)
CONF="/etc/nginx/sites-available/$DOMAIN"
echo "Writing $CONF..."
cat > "$CONF" << EOF
server {
    listen 80 default_server;
    server_name $DOMAIN _;
    location / {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

# Enable and test
ln -sf "$CONF" /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
echo "Nginx configured. http://$DOMAIN should proxy to port $BACKEND_PORT."

# Optional: HTTPS with Let's Encrypt
if [ "$DO_HTTPS" = true ]; then
    if ! command -v certbot &>/dev/null; then
        echo "Installing certbot..."
        apt-get install -y certbot python3-certbot-nginx
    fi
    echo "Running certbot for $DOMAIN (you may be prompted for email and terms)..."
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email || \
    certbot --nginx -d "$DOMAIN"
    echo "HTTPS enabled. https://$DOMAIN should work."
fi

echo "Done."
