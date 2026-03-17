# AstroPlatform Deployment Guide (for OpenClaw / AI Agent)

## Overview

Deploy AstroPlatform as a public website:
- **Backend**: Python FastAPI → deploy to **Render.com** (free tier, simpler than Railway)
- **Frontend**: React Vite → deploy to **Cloudflare Pages** (free, fast, no VPN needed)
- **Database**: PostgreSQL → Render provides free PostgreSQL

GitHub repo: https://github.com/MikhailXiaomaikou/AstroPlatform

---

## STEP 1: Deploy Backend to Render.com

### 1a. Create Render account
- Go to https://render.com
- Sign up with GitHub (MikhailXiaomaikou account)

### 1b. Create PostgreSQL database
- Dashboard → "New" → "PostgreSQL"
- Name: `astro-db`
- Region: Oregon (or nearest)
- Plan: Free
- Click "Create Database"
- **Copy the "Internal Database URL"** (format: `postgresql://user:pass@host/dbname`)

### 1c. Create Web Service for backend
- Dashboard → "New" → "Web Service"
- Connect GitHub repo: `MikhailXiaomaikou/AstroPlatform`
- Settings:
  - **Name**: `astro-backend`
  - **Region**: same as database
  - **Root Directory**: `backend`
  - **Runtime**: Docker
  - **Plan**: Free

### 1d. Set environment variables
In the web service settings → "Environment" tab, add:

```
ENV=production
DATABASE_URL=<paste the Internal Database URL from step 1b>
JWT_SECRET=1c4df3e47d07142c5c73afa5e79847920897624480012a1f185569e6167d550f
CORS_ORIGINS=https://astroplatform.pages.dev
RATE_LIMIT_ENABLED=false
PORT=8000
```

Note: `CORS_ORIGINS` will be updated after frontend deploy.

### 1e. Deploy
- Click "Create Web Service"
- Wait for build + deploy (may take 5-10 minutes)
- Backend URL will be like: `https://astro-backend-xxxx.onrender.com`
- Verify: visit `https://astro-backend-xxxx.onrender.com/health` → should return `{"status":"ok"}`

---

## STEP 2: Deploy Frontend to Cloudflare Pages

### 2a. Create Cloudflare account
- Go to https://dash.cloudflare.com/sign-up
- Sign up with email

### 2b. Create Pages project
- Sidebar → "Workers & Pages" → "Create"
- Tab: "Pages" → "Connect to Git"
- Connect GitHub → select `MikhailXiaomaikou/AstroPlatform`
- Build settings:
  - **Project name**: `astroplatform`
  - **Production branch**: `main`
  - **Root directory (path)**: `frontend`
  - **Framework preset**: None (or Vite if available)
  - **Build command**: `npm run build`
  - **Build output directory**: `dist`

### 2c. Set environment variable
- Add environment variable:
  - **Variable name**: `VITE_API_URL`
  - **Value**: `https://astro-backend-xxxx.onrender.com` (the Render backend URL from step 1e)

### 2d. Deploy
- Click "Save and Deploy"
- Frontend URL will be: `https://astroplatform.pages.dev`

---

## STEP 3: Connect Frontend ↔ Backend (CORS)

Go back to Render.com → `astro-backend` service → Environment:
- Update `CORS_ORIGINS` to the actual Cloudflare Pages URL:
  ```
  CORS_ORIGINS=https://astroplatform.pages.dev
  ```
- Render will auto-redeploy

---

## STEP 4: Generate Setup Keys for Beta Testers

Once backend is live, run:

```bash
curl -X POST https://astro-backend-xxxx.onrender.com/api/auth/generate-setup-keys \
  -H "Content-Type: application/json" \
  -d '{"count": 10, "label": "beta"}'
```

This returns 10 setup keys like `ASTRO-BETA-XXXXXX` that users can use to log in.

---

## STEP 5: Verify Everything Works

1. Open `https://astroplatform.pages.dev` → should load the app
2. Click "Sign In" → "Have a setup key?" → enter a generated key → should log in
3. Search for "M31" in Data Browser → should return results
4. Go to Settings → set an Anthropic API key → AI Assistant should work

---

## Troubleshooting

### Backend build fails with scipy/gfortran error
The Dockerfile uses `--only-binary=:all:` flag to avoid compiling from source. If it still fails, the `requirements.txt` already uses `>=` version constraints to allow pip to find compatible pre-built wheels.

### CORS errors in browser console
Make sure `CORS_ORIGINS` on Render matches the exact Cloudflare Pages URL (including https://).

### Database connection fails
Make sure `DATABASE_URL` uses the **Internal** URL (not External) if both services are on Render. The backend auto-converts `postgresql://` to `postgresql+asyncpg://`.

### Free tier cold starts
Render free tier sleeps after 15 min of inactivity. First request after sleep takes ~30 seconds. This is normal for free tier.

---

## Alternative: Use Railway instead of Render

If using Railway (https://railway.app):
1. New Project → Deploy from GitHub Repo → `AstroPlatform`
2. Click the service → Settings → set Root Directory to `backend`
3. Settings → Builder → select "Dockerfile" if available
4. Variables → add same env vars as above
5. Add PostgreSQL: click "+ New" → Database → PostgreSQL
6. Settings → Networking → Generate Domain

The Railway project may already exist (was partially set up). Check https://railway.app/dashboard first.
