# Deployment Guide: Vercel + Railway

## Architecture

```
[User Browser]
      |
      v
[Vercel - Frontend]  ──API calls──>  [Railway - Backend]
   (React SPA)                          (FastAPI + PostgreSQL)
```

## Step 1: Deploy Backend on Railway

1. Go to [railway.app](https://railway.app) and sign in with GitHub

2. Click **"New Project"** → **"Deploy from GitHub repo"**

3. Select your repo, set the **root directory** to `backend`

4. Add a **PostgreSQL** plugin (click "+ New" → "Database" → "PostgreSQL")

5. Railway auto-sets `DATABASE_URL`. Add these **environment variables**:

   | Variable | Value |
   |----------|-------|
   | `ENV` | `production` |
   | `JWT_SECRET` | (generate: `openssl rand -hex 32`) |
   | `CORS_ORIGINS` | `https://your-app.vercel.app` (set after Vercel deploy) |
   | `ANTHROPIC_API_KEY` | (optional, users can set their own in Settings) |
   | `RATE_LIMIT_ENABLED` | `true` |

6. Click **Deploy**. Note the generated URL (e.g. `https://astro-backend-xxx.up.railway.app`)

7. Verify: visit `https://your-railway-url/health` → should return `{"status":"ok"}`

## Step 2: Deploy Frontend on Vercel

1. Go to [vercel.com](https://vercel.com) and sign in with GitHub

2. Click **"Add New Project"** → Import your repo

3. Set:
   - **Root Directory**: `frontend`
   - **Framework Preset**: Vite
   - **Build Command**: `npm run build`
   - **Output Directory**: `dist`

4. Add **environment variable**:

   | Variable | Value |
   |----------|-------|
   | `VITE_API_URL` | `https://astro-backend-xxx.up.railway.app` |

5. Click **Deploy**

6. Note the Vercel URL (e.g. `https://your-app.vercel.app`)

## Step 3: Connect Frontend ↔ Backend

1. Go back to **Railway** → your backend service → **Variables**

2. Update `CORS_ORIGINS` to your actual Vercel URL:
   ```
   CORS_ORIGINS=https://your-app.vercel.app
   ```

3. Redeploy the backend (Railway auto-redeploys on variable change)

## Step 4: Generate Setup Keys

```bash
curl -X POST https://your-railway-url/api/auth/generate-setup-keys \
  -H "Content-Type: application/json" \
  -d '{"count": 10, "label": "beta"}'
```

Distribute the keys to beta testers. They enter the key on the login page to get access.

## Verify

- [ ] Frontend loads at Vercel URL
- [ ] Can log in with setup key
- [ ] Search returns results
- [ ] AI Assistant responds (if ANTHROPIC_API_KEY set, or user sets own key in Settings)

## Custom Domain (Optional)

- **Vercel**: Settings → Domains → Add your domain
- **Railway**: Settings → Networking → Custom Domain
- Update `CORS_ORIGINS` to match the new domain

## Costs (Estimated)

| Service | Free Tier | Paid |
|---------|-----------|------|
| Vercel | 100GB bandwidth/mo | $20/mo (Pro) |
| Railway | $5 credit/mo | ~$5-15/mo (PostgreSQL + compute) |
| **Total** | **~$0-5/mo for beta** | **~$25-35/mo** |
