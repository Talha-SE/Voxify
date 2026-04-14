# Voxify Deployment Guide (Oracle Free Tier + Ubuntu 24.04)

This guide deploys the Voxify website and API on Oracle Cloud with:

- Domain: `voxify.brevios.com`
- Reverse proxy: Nginx
- App runtime: Flask app served by Gunicorn
- Process manager: systemd
- SSL: Let's Encrypt (Certbot)
- License storage: MongoDB Atlas

The repo contains both desktop app and website code. On Oracle, you deploy the `website/` server and optionally host desktop release ZIPs from the `release/` folder.

---

## 0) Prerequisites

Before running commands:

1. Point DNS `A` record:
   - `voxify.brevios.com` -> your Oracle instance public IP.
2. In MongoDB Atlas:
   - Add your Oracle public IP to Network Access.
3. Keep ready:
   - Mistral API key
   - Gumroad product IDs / API token
   - Admin username/password

---

## 1) Connect to Server

You already connect as:

```bash
ssh -i /path/to/your/key ubuntu@YOUR_SERVER_PUBLIC_IP
```

All commands below assume user `ubuntu`.

---

## 2) Base Setup (One-Time)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git ufw
```

Enable firewall (safe profile):

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw --force enable
sudo ufw status
```

---

## 3) Clone Voxify Repository

```bash
cd /home/ubuntu
git clone https://github.com/Talha-SE/Voxify.git
cd /home/ubuntu/Voxify
```

---

## 4) Python Environment + Dependencies

Create one virtual environment in repo root and install website dependencies:

```bash
cd /home/ubuntu/Voxify
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -r website/requirements.txt gunicorn
```

---

## 5) Configure Production Environment

Create `.env` for website:

```bash
cd /home/ubuntu/Voxify
cp website/.env.example website/.env
nano website/.env
```

Set secure secrets quickly:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('ReplaceWithStrongAdminPassword'))"
```

Paste this template and replace values:

```env
# Core API
MISTRAL_API_KEY=YOUR_MISTRAL_API_KEY
MISTRAL_MODEL=voxtral-mini-2507

# Gumroad checkout links
GUMROAD_MEMBERSHIP_URL=https://brevios.gumroad.com/l/voxify-membership
GUMROAD_ONETIME_URL=https://brevios.gumroad.com/l/voxify

# Gumroad product mapping
GUMROAD_PRODUCT_ID=prod_xxx_membership
GUMROAD_PRODUCT_ID_STARTER=prod_xxx_starter
GUMROAD_PRODUCT_ID_PRO=prod_xxx_pro
GUMROAD_PRODUCT_ID_TEAM=prod_xxx_team
GUMROAD_PRODUCT_ID_LIFETIME=prod_xxx_lifetime
GUMROAD_API_ACCESS_TOKEN=YOUR_GUMROAD_ACCESS_TOKEN

# Optional: variant-based mapping for one-product multi-variant setups
# VOXIFY_GUMROAD_VARIANT_RULES_JSON={"monthly starter":{"planCode":"starter","billingCycle":"monthly","priceType":"membership","seatLimit":1}}

# Admin login
ADMIN_USERNAME=brevios_admin
ADMIN_PASSWORD_HASH=PASTE_GENERATED_HASH

# Flask
FLASK_SECRET_KEY=PASTE_LONG_RANDOM_SECRET
SESSION_SECURE_COOKIE=true
PORT=5050

# MongoDB Atlas
MONGODB_URI=mongodb+srv://USER:PASSWORD@CLUSTER.mongodb.net/?retryWrites=true&w=majority
MONGODB_DB_NAME=voxify

# Optional license token behavior
# VOXIFY_LICENSE_TOKEN_MAX_AGE_SEC=604800
# VOXIFY_LICENSE_REFRESH_INTERVAL_SEC=21600
# VOXIFY_ALLOW_CLIENT_LIVE_KEY=true
```

---

## 6) (Optional but Recommended) Upload Desktop Release Artifacts

The website serves app downloads from `/home/ubuntu/Voxify/release` through:

- `/download/windows`
- `/download/macos`
- `/download/linux`

Create folder (if missing):

```bash
mkdir -p /home/ubuntu/Voxify/release
```

Expected filenames:

- `Voxify-v1.0.0-windows.zip`
- `Voxify-v1.0.0-macos.zip`
- `Voxify-v1.0.0-linux.zip`

From your local machine, upload files:

```bash
scp Voxify-v1.0.0-windows.zip ubuntu@YOUR_SERVER_PUBLIC_IP:/home/ubuntu/Voxify/release/
scp Voxify-v1.0.0-macos.zip ubuntu@YOUR_SERVER_PUBLIC_IP:/home/ubuntu/Voxify/release/
scp Voxify-v1.0.0-linux.zip ubuntu@YOUR_SERVER_PUBLIC_IP:/home/ubuntu/Voxify/release/
```

---

## 7) Create systemd Service (Gunicorn)

```bash
sudo tee /etc/systemd/system/voxify.service >/dev/null <<'EOF'
[Unit]
Description=Voxify Website (Flask + Gunicorn)
After=network.target

[Service]
User=ubuntu
Group=www-data
WorkingDirectory=/home/ubuntu/Voxify/website
Environment="PATH=/home/ubuntu/Voxify/.venv/bin"
ExecStart=/home/ubuntu/Voxify/.venv/bin/gunicorn --workers 2 --threads 4 --timeout 120 --bind 127.0.0.1:5050 server:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Enable and start service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now voxify
sudo systemctl status voxify --no-pager
```

Live logs:

```bash
journalctl -u voxify -f
```

---

## 8) Configure Nginx for voxify.brevios.com

```bash
sudo tee /etc/nginx/sites-available/voxify >/dev/null <<'EOF'
server {
    listen 80;
    listen [::]:80;
    server_name voxify.brevios.com;

    # Batch transcription uploads can be larger than default 1M.
    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
        proxy_send_timeout 300;
    }
}
EOF
```

Enable site and reload Nginx:

```bash
sudo ln -sf /etc/nginx/sites-available/voxify /etc/nginx/sites-enabled/voxify
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

---

## 9) Enable HTTPS (Let's Encrypt)

```bash
sudo certbot --nginx -d voxify.brevios.com --redirect -m you@example.com --agree-tos --no-eff-email
sudo certbot renew --dry-run
```

After SSL is active, confirm in `.env`:

- `SESSION_SECURE_COOKIE=true`

Then restart app:

```bash
sudo systemctl restart voxify
```

---

## 10) Post-Deploy Verification

```bash
curl -I https://voxify.brevios.com/
curl https://voxify.brevios.com/api/site-status
curl -I https://voxify.brevios.com/download/windows
```

Open in browser:

- `https://voxify.brevios.com/`
- `https://voxify.brevios.com/admin-brevios-login`

---

## 11) Update Workflow (After New GitHub Push)

```bash
cd /home/ubuntu/Voxify
git pull origin main
source .venv/bin/activate
pip install -r website/requirements.txt gunicorn
sudo systemctl restart voxify
sudo systemctl status voxify --no-pager
```

If your default branch is not `main`, replace it accordingly.

---

## 12) Fast Troubleshooting

### 502 Bad Gateway

```bash
sudo systemctl status voxify --no-pager
journalctl -u voxify -n 120 --no-pager
```

### Certbot cannot reach the domain on port 80

If `curl -I http://localhost` works but `curl -I http://voxify.brevios.com` fails from the Oracle box, the problem is usually outside Nginx:

- Check Namecheap DNS: `voxify` must be an `A` record that points to the Oracle public IPv4.
- Remove any `AAAA` record unless IPv6 is actually configured and reachable.
- In Oracle Cloud, add ingress rules for TCP `80` and `443` from `0.0.0.0/0` to the instance subnet or network security group.
- Do not rely on self-testing the public hostname from the same server as the only proof. Confirm the domain from an external network too.

Useful checks:

```bash
curl -I http://127.0.0.1
curl -I http://$(curl -s ifconfig.me)
dig +short voxify.brevios.com A
dig +short voxify.brevios.com AAAA
```

### MongoDB connection errors

- Confirm Atlas IP whitelist includes your Oracle public IP.
- Confirm `MONGODB_URI` and credentials.

### Upload too large / 413

- Increase `client_max_body_size` in Nginx and reload.

### Nginx syntax or TLS issues

```bash
sudo nginx -t
sudo systemctl status nginx --no-pager
```

---

## 13) Useful Paths

- Repo root: `/home/ubuntu/Voxify`
- Website app: `/home/ubuntu/Voxify/website/server.py`
- Environment file: `/home/ubuntu/Voxify/website/.env`
- Release files: `/home/ubuntu/Voxify/release`
- systemd unit: `/etc/systemd/system/voxify.service`
- Nginx site: `/etc/nginx/sites-available/voxify`
