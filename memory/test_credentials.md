# ASTRIX AI — Test Credentials & Environment

## Live Backend
- Frontend: https://b70ca5dc-b819-4ce8-b854-d49778beb5c5.preview.emergentagent.com
- Backend API: same domain, `/api/*` prefixed endpoints
- Backend health: `GET /api/health`

## Supabase
- Project: erqxtlhvpeqvgpzvzaxv
- URL: https://erqxtlhvpeqvgpzvzaxv.supabase.co
- Anon key (public): in `/app/frontend/.env`
- Service role key (server-only): in `/app/backend/.env`
- Personal Access Token (admin): `sbp_4d88048a252a8b8981b462569f1ca1f44dbf93a7`

## Test User Creation (programmatic — for testing agent)
Use Supabase admin endpoint to create a verified test user (no email confirmation needed):

```
POST https://erqxtlhvpeqvgpzvzaxv.supabase.co/auth/v1/admin/users
Headers:
  apikey: <SERVICE_ROLE_KEY>
  Authorization: Bearer <SERVICE_ROLE_KEY>
  Content-Type: application/json
Body:
  {"email":"test+<timestamp>@astrixai.app","password":"TestPass123!","email_confirm":true,
   "user_metadata":{"full_name":"Test User"}}
```

Then sign in via:
```
POST https://erqxtlhvpeqvgpzvzaxv.supabase.co/auth/v1/token?grant_type=password
Headers: apikey: <ANON_KEY>, Content-Type: application/json
Body: {"email":"...","password":"TestPass123!"}
```
Returns `access_token` JWT — use as `Authorization: Bearer <token>` for backend `/api/*` calls.

## Suggested test user
- email: `test@astrixai.app`
- password: `TestPass123!`
(Create via admin endpoint above before each test run — or sign up via UI; emails go to Resend sandbox if domain unverified)

## Backend AI quota
- Free plan: 100 AI calls/month per workspace
- Reset: 1st of each month
- 429 response when exceeded

## Dodo Payments (LIVE mode)
- API: https://live.dodopayments.com
- Product IDs in `pricing_config.stripe_price_id`:
  - starter_monthly: pdt_0NbFfMOQIsJF9X0LrtkH9 ($59/mo)
  - starter_annual:  pdt_0NbC7p1x3vArb3CYIqAT6 ($588/yr)
  - growth_monthly:  pdt_0NbC3RvgjyFoZ6wJ7LLqP ($179/mo)
  - growth_annual:   pdt_0NbC5NQsoxq2leqmoeDmB ($1,789/yr)
  - scale_monthly:   pdt_0NcBuLfSXkF4hSJFs0AbV ($449/mo)
  - scale_annual:    pdt_0NbFhELrTC3P4kNf8On24 ($4,489/yr)
- Webhook endpoint: `POST /api/billing/webhook` (verifies standard-webhooks signature)

## Resend Email
- From: `ASTRIX AI <team@astrixai.app>` (domain `astrixai.app` configured)
- Used for: invitations, launch reminders (Day 7/30)

## Sentry
- DSN configured in both frontend (`VITE_SENTRY_DSN`) and backend (`SENTRY_DSN`)
- Auto-captures exceptions

## Cron / Reminders
- Endpoint: `POST /api/cron/send-launch-reminders`
- Auth: `x-cron-secret: <DODO_WEBHOOK_SECRET>` (re-uses webhook secret as cron secret)
- Should be hit daily by external scheduler (cron-job.org / Cloudflare cron)
