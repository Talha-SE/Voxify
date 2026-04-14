# Full Deployment Guide: Oracle Cloud & Vercel

This guide covers everything from renaming your server to deploying the full stack.

---

## 🛑 PART 1: PRE-DEPLOYMENT (Server Renaming)

**User Question:** *"Can I change `ubuntu@air-translator` to `@brevios`? Will it affect my files?"*

**Answer:** Yes, you can safely change the hostname. **It will NOT delete or affect any of your files.** It only changes the system's name label.

### 📝 Steps to Rename Server to `brevios`

Run these commands one by one on your Oracle server:

1.  **Set the new hostname:**
    ```bash
    sudo hostnamectl set-hostname brevios
    ```

2.  **Update the hosts file:**
    Open the hosts file:
    ```bash
    sudo nano /etc/hosts
    ```
    Find the line that says `127.0.0.1 localhost` or similar. Add `brevios` to the end of that line, or replace the old name (`air-translator`) with `brevios`.
    *Example:* `127.0.0.1 localhost brevios`
    *Press `Ctrl+O` then `Enter` to save, and `Ctrl+X` to exit.*

3.  **Apply changes:**
    Reboot the server to see the change fully apply (optional but recommended):
    ```bash
    sudo reboot
    ```
    *After logging back in, your prompt should look like: `ubuntu@brevios:~$`*

---

## ☁️ PART 2: BACKEND DEPLOYMENT (Oracle Cloud)

### 1. Essentials Installation
Update system and install Node.js, PM2, and Git.

```bash
# Update System
sudo apt update && sudo apt upgrade -y

# Install Node.js 20 (LTS)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install PM2 (Process Manager)
sudo npm install -g pm2

# Install Nginx (Web Server)
sudo apt install nginx -y

# Install Git
sudo apt install git -y
```

### 2. Database Setup (MongoDB Atlas - Recommended)
Since you are using a **MongoDB Atlas Cluster**, you do not need to install MongoDB on your Oracle server.

1.  Log in to [MongoDB Atlas](https://cloud.mongodb.com/).
2.  Go to **Database** -> **Connect** -> **Drivers**.
3.  Copy your **Connection String** (it looks like `mongodb+srv://<username>:<password>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority`).
4.  **Crucial**: Go to **Network Access** in Atlas and click **"Add IP Address"**. 
    *   Add your **Oracle Server Public IP** to the whitelist.
    *   Or add `0.0.0.0/0` (not recommended for production, but easiest for testing).

---

# 3. Clone & Setup Backend
Run these commands to pull your code. **Note:** Git will automatically create the `ONNE` folder for you.

```bash
# Clone the repository (This creates the 'ONNE' folder automatically)
git clone https://github.com/Talha-SE/ONNE.git
cd ONNE/backend

# Install dependencies
npm install --production

# Configure Environment Variables
# Create a .env file
nano .env
```

**Paste your environment variables into the nano editor:**
```env
# Discord Bot Configuration
DISCORD_TOKEN=your_discord_bot_token_here
DISCORD_CLIENT_ID=your_discord_client_id_here
DISCORD_CLIENT_SECRET=your_discord_client_secret_here

# MongoDB Configuration
MONGODB_URI=mongodb+srv://user:password@your-cluster.mongodb.net/database_name

# API Configuration
PORT=3001
NODE_ENV=production
API_URL=http://your-server-domain.duckdns.org

# Frontend URL (for CORS)
FRONTEND_URL=https://your-frontend-domain.com

# JWT Secret for Dashboard Authentication
JWT_SECRET=your_random_jwt_secret_here

# Mistral AI Configuration
MISTRAL_API_KEY=your_mistral_api_key_here
MISTRAL_MODEL=mistral-small-latest

# Top.gg Configuration
TOPGG_TOKEN=your_topgg_token_here
TOPGG_WEBHOOK_AUTH=your_webhook_password_here
```
*Press `Ctrl+O` -> `Enter` -> `Ctrl+X` to save and exit.*

### 4. Start Backend with PM2
```bash
# Register slash commands first
node src/utils/deploy-commands.js

# Start the app
pm2 start src/index.js --name "onne-backend"

# Save list so it restarts on reboot
pm2 save
pm2 startup
```

### 5. Configure Nginx (DuckDNS Setup)
Make your API accessible via your DuckDNS domain.

```bash
# Create config file
sudo nano /etc/nginx/sites-available/onne-api
```

**Paste this configuration:**
```nginx
server {
    listen 80;
    server_name onne-server.duckdns.org; # 👈 Your DuckDNS domain

    location / {
        proxy_pass http://localhost:3001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
}
```

**Activate & Restart Nginx:**
```bash
sudo ln -s /etc/nginx/sites-available/onne-api /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

## 🚀 PART 3: FRONTEND DEPLOYMENT (Vercel)

Vercel is the easiest way to host Next.js apps.

### 1. Import Project
1.  Go to [Vercel Dashboard](https://vercel.com/dashboard).
2.  Click **"Add New..."** -> **"Project"**.
3.  Connect your GitHub account.
4.  Select your repository (`SERVERIQ` or `onne`).

### 2. Configure Project Settings
Vercel will detect it's a Next.js app, but we need to tell it where the frontend is.

*   **Framework Preset:** Next.js
*   **Root Directory:** Click "Edit" and select `frontend` (since your repo has both backend/frontend folders).

### 3. Environment Variables
Click **"Environment Variables"** and add these:

| Key | Value |
| :--- | :--- |
| NEXT_PUBLIC_API_URL | http://onne-server.duckdns.org |
| NEXT_PUBLIC_APP_NAME | ONNE |
| NEXT_PUBLIC_APP_URL | https://onne.brevios.com |

### 4. Deploy
Click **"Deploy"**. Vercel will build your site and give you a URL.

---

## 🔄 PART 4: UPDATING CODE

### Updating Backend (Oracle)
When you push changes to GitHub:
```bash
cd ~/ONNE/backend
git pull
npm install # Only if you added new packages
pm2 restart onne-backend
```

### Updating Frontend (Vercel)
Vercel automatically redeploys when you push to GitHub! No action needed.
