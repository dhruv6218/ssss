# ASTRIX AI — Deployment Guide

## Architecture for Production
- **Frontend**: Static SPA on Vercel/Netlify (free)
- **Backend**: FastAPI deployed on Render/Fly.io/Railway/your VPS (free tier OK)
- **Database**: Supabase (free tier)
- **Domain**: astrixai.app (your Resend-verified domain)

---

## STEP 1 — Deploy Backend (Render free tier — recommended)

1. Push `/app/backend` to a GitHub repo (e.g., `astrix-backend`)
2. Go to https://render.com → New → Web Service → connect repo
3. Settings:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn server:app --host 0.0.0.0 --port $PORT`
   - Environment variables — copy ALL from `/app/backend/.env`:
     ```
     SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY, SUPABASE_JWT_SECRET,
     GEMINI_API_KEY, OPENROUTER_API_KEY, GROK_API_KEY,
     RESEND_API_KEY, RESEND_FROM_EMAIL, RESEND_DOMAIN,
     DODO_API_KEY, DODO_WEBHOOK_SECRET, DODO_MODE=live, DODO_API_BASE=https://live.dodopayments.com,
     SENTRY_DSN, APP_URL=https://your-vercel-url.vercel.app
     ```
4. Deploy. Note the URL (e.g., `https://astrix-backend.onrender.com`)

## STEP 2 — Deploy Frontend (Vercel — easiest)

1. Push `/app/frontend` to a GitHub repo (e.g., `astrix-frontend`)
2. Go to https://vercel.com → New Project → Import repo
3. Framework Preset: **Vite**
4. Environment variables (Project Settings → Environment Variables):
   ```
   VITE_SUPABASE_URL=https://erqxtlhvpeqvgpzvzaxv.supabase.co
   VITE_SUPABASE_ANON_KEY=<paste anon key from /app/frontend/.env>
   VITE_BACKEND_URL=https://astrix-backend.onrender.com
   REACT_APP_BACKEND_URL=https://astrix-backend.onrender.com
   VITE_SENTRY_DSN=<your sentry DSN>
   VITE_POSTHOG_KEY=<your posthog key>
   VITE_POSTHOG_HOST=https://us.i.posthog.com
   ```
5. **Edit `/app/frontend/vercel.json`** — replace `YOUR_BACKEND_URL` with your Render URL (no `https://` prefix needed in destination, just full URL).
6. Deploy. Note the URL (e.g., `https://astrix.vercel.app`)

### Alternative: Netlify
- Same as above but use `/app/frontend/netlify.toml` (replace `YOUR_BACKEND_URL`)
- Drag-drop `/app/frontend/dist` after `yarn build` for fastest deploy

## STEP 3 — Configure Supabase Auth

Go to https://supabase.com/dashboard/project/erqxtlhvpeqvgpzvzaxv/auth/url-configuration and set:

**Site URL**: `https://astrix.vercel.app` (your Vercel URL)

**Redirect URLs** (add all):
- `https://astrix.vercel.app/onboarding/step-1`
- `https://astrix.vercel.app/reset-password`
- `https://astrix.vercel.app/accept-invitation`
- `https://astrix.vercel.app/app`

## STEP 4 — Configure Dodo Webhook

Go to https://app.dodopayments.com/webhooks → Add endpoint:
- URL: `https://astrix-backend.onrender.com/api/billing/webhook`
- Events: `subscription.active`, `subscription.created`, `subscription.cancelled`, `subscription.expired`, `subscription.failed`, `payment.succeeded`, `payment.failed`
- Use the same `DODO_WEBHOOK_SECRET` you have in backend `.env`

## STEP 5 — Set up Cron for Launch Reminders (free)

Use https://cron-job.org (free):
- URL: `https://astrix-backend.onrender.com/api/cron/send-launch-reminders`
- Schedule: Daily at 9 AM UTC
- Custom HTTP header: `x-cron-secret: <DODO_WEBHOOK_SECRET>` (re-used)

## STEP 6 — Custom domain (optional)

In Vercel: Settings → Domains → Add `astrixai.app` → follow DNS instructions
Then update Supabase Auth Site URL + redirects + Dodo webhook URL accordingly.

---

## Production checklist ✅

- [x] RLS enabled on every workspace-scoped table
- [x] JWT signature verification (HS256 with `SUPABASE_JWT_SECRET`)
- [x] Webhook signature verification (rejects on missing headers)
- [x] AI quota per workspace (free=100/mo, paid up to 10000/mo)
- [x] Pydantic Literal enums on all enum fields
- [x] Bleach sanitization on all text inputs
- [x] CSV size cap: max 10,000 rows
- [x] Audit logging on critical mutations (`activity_logs` table)
- [x] Sentry on frontend + backend
- [x] CORS configured
- [x] Strict security headers (vercel.json + netlify.toml)
- [x] Long-cache for static assets
- [x] Free subscription auto-created on workspace insert
- [x] Email verification soft-gate (Supabase `email_confirmed_at`)

## Ongoing maintenance

- Rotate Supabase service-role key every 90 days
- Watch Sentry for error spikes
- Monitor `ai_provider_usage` table for quota patterns; tune `plan_limits` accordingly
- Run `pytest /app/backend/tests/` before each deploy
