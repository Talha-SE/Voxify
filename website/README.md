# Brevios Website Module

This folder contains a separate website with server-side secret management.

## What is secured here

- `MISTRAL_API_KEY` is loaded from `.env` only on the server.
- Admin login credentials are loaded from `.env`.
- Gumroad checkout links are loaded from `.env`.
- License and usage data are stored in MongoDB (`MONGODB_URI`, `MONGODB_DB_NAME`).
- No user signup or user login routes are exposed on the public website.

## Pages included

- `/` main landing page
- `/privacy-policy` privacy policy page
- `/terms-and-conditions` terms and conditions page
- `/admin-brevios-login` hidden admin login page
- `/admin-brevios-dashboard` admin dashboard after login
- `/api/verify-license` backend endpoint for Gumroad license verification
- `/api/license/activate`, `/api/license/refresh`, `/api/license/status`
- `/api/license/consume` quota accounting endpoint
- `/api/transcribe` server-side transcription proxy for batch mode
- `/api/gumroad/webhook` webhook status sync endpoint

## Run locally

1. Open terminal in this folder.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start server:

```bash
python server.py
```

4. Open:

- `http://127.0.0.1:5050/`
- `http://127.0.0.1:5050/admin-brevios-login`

## Gumroad setup

1. Create a Gumroad Membership product and enable unique license keys.
2. Create one-time product (or lifetime plan) for non-recurring users.
3. Put URLs and product details in `.env`:

```env
GUMROAD_MEMBERSHIP_URL=https://yourname.gumroad.com/l/your-membership
GUMROAD_ONETIME_URL=https://yourname.gumroad.com/l/your-onetime-plan
GUMROAD_PRODUCT_ID=your_gumroad_product_id
GUMROAD_API_ACCESS_TOKEN=
MONGODB_URI=mongodb://127.0.0.1:27017
MONGODB_DB_NAME=sonus
```

## Desktop app license validation flow

Your desktop app can call this API:

`POST /api/verify-license`

Example JSON body:

```json
{
	"licenseKey": "USER_LICENSE_KEY",
	"productId": "YOUR_PRODUCT_ID"
}
```

If verification succeeds, the endpoint returns `success: true` and `active: true`.
If a subscription is inactive or invalid, it returns `active: false`.

## Important

- Change `FLASK_SECRET_KEY` in `.env` before production deployment.
- Regenerate `ADMIN_PASSWORD_HASH` with your own password.
- Keep `.env` private and never commit it.
