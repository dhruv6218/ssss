# ASTRIX AI — Product Requirements Document (Live)

## Original Problem Statement
Build the ASTRIX AI MVP — a B2B SaaS Product Decision & Outcome Intelligence Platform. User provided a complete frontend repo (Vite + React + TypeScript) and asked to build the Supabase backend, wire it end-to-end, add billing (Dodo Payments LIVE), email (Resend), AI (Gemini → OpenRouter → Grok fallback), and make it 100% production-ready, secured, and protected. Full MVP loop: **Signals → Problems → Opportunities → Decisions → Launches → Verdicts**.

## Architecture
- **Frontend**: Vite + React 19 + TypeScript on port 3000 (`/app/frontend`)
- **Backend**: FastAPI Python on port 8001 (`/app/backend`) — AI orchestration, billing, email, scoring
- **Database + Auth**: Supabase (Postgres + Auth + RLS) — project `erqxtlhvpeqvgpzvzaxv`
- **AI**: Gemini 2.5 Flash (primary, user's free Google API key), OpenRouter (fallback free tier), Grok via Groq (paid tier)
- **Billing**: Dodo Payments (LIVE mode) with webhook signature verification
- **Email**: Resend (`team@astrixai.app`, domain `astrixai.app`)
- **Errors**: Sentry on both frontend & backend

## User Personas
- **Alex (primary)**: Head of Product at Series A B2B SaaS, 30–100 employees
- Founders/CEOs, VP Product, Support Lead, CS Lead (secondary)

## Core MVP Loop ✅ All Implemented
1. Signup (email/password or Google OAuth)
2. Create workspace (auto-bootstrapped via `create_workspace_with_owner` RPC, free subscription auto-created)
3. Upload signals & accounts CSVs (or seed sample workspace) — bulk-inserts to Supabase with RLS
4. AI auto-classifies signals (severity, sentiment, product area)
5. Group signals into problems
6. System scores opportunities (5-component model totaling 100: demand 30, pain 20, ARR 30, severity 10, recency 10)
7. Create decisions with rationale (Build / Fix / Experiment / Defer / Reject)
8. AI drafts decision memo or PRD
9. Create launch from decision
10. Day 7 + Day 30 review checkpoints (via `launch_reviews` table)
11. Submit final verdict (Solved / Partially Solved / Not Solved / Regressed)
12. AI auto-generates proof summary card

## What's Implemented (April 2026)

### Database (Supabase)
- 32 tables total (16 mine + 16 user pre-existing) with RLS enforced workspace-scoped isolation
- Helper RPCs: `create_workspace_with_owner`, `get_workspace_plan`, `check_and_increment_ai_quota`, `is_workspace_member`, `is_workspace_admin`, `log_activity`, `ensure_free_subscription` (trigger)
- Seeded `pricing_config` with 6 real Dodo product IDs (3 plans × monthly/annual)
- Seeded `plan_limits` with 4 tiers (free/starter/growth/scale)

### Backend (FastAPI)
- 17 routes covering AI, scoring, billing, team, cron, health
- AI fallback chain (Gemini → OpenRouter → Grok)
- Per-workspace monthly AI quota enforcement via Postgres RPC
- Bleach input sanitization, Pydantic Literal/Enum validation
- Supabase JWT verification on every privileged route
- Workspace membership + admin role checks on writes
- Audit logging via `activity_logs` on critical mutations
- Sentry integration

### Frontend (Vite + React)
- Real Supabase client (replaced dummy)
- Real AuthContext with email/pw + Google OAuth
- Real WorkspaceContext using `create_workspace_with_owner` RPC
- Real api.ts with React hooks for all 9 entities (signals, accounts, problems, opportunities, decisions, artifacts, launches, team, billing)
- Real CSV ingestion that bulk-inserts to Supabase with domain matching for accounts
- Pricing page → real Dodo checkout flow (LIVE mode)
- Settings → real subscription status + quick upgrade buttons + real audit log
- AcceptInvitation → real backend endpoint with token verification
- Step3 onboarding → real `seed-sample` API

### Billing (Dodo LIVE)
- `POST /api/billing/checkout` creates real subscription + returns `checkout.dodopayments.com/*` URL
- `POST /api/billing/webhook` verifies standard-webhooks signature; rejects on missing headers
- `GET /api/billing/status/{ws}` returns current plan + subscription state
- `GET /api/billing/pricing` lists active plans
- Auto-creates free subscription on workspace creation

### Email (Resend)
- Invitation emails with accept URL
- Launch reminder emails (Day 7 + Day 30) — driven by `/api/cron/send-launch-reminders`
- From: `ASTRIX AI <team@astrixai.app>`

## Hardening Status
| Item | Status |
|---|---|
| Workspace data isolation (RLS) | ✅ Verified |
| AI quota per workspace (free=100/mo, paid=500–10000) | ✅ Verified |
| Input validation + sanitization | ✅ Pydantic + bleach |
| Audit log for critical mutations | ✅ activity_logs + log_activity RPC |
| Sentry error tracking | ✅ Frontend + backend |
| CSV upload size limit | ⚠️ TODO (10MB enforce) |
| Email verification gate | ⚠️ Soft (Supabase `email_confirm` flag) |
| Admin/owner role enforcement | ✅ is_workspace_admin RLS gating |
| Webhook signature verification | ✅ Strict — rejects on missing headers |
| Pydantic Literal enums for invite/checkout | ✅ |

## Tested (Iteration 1)
- 21/21 backend tests passed (100%)
- Real Dodo LIVE checkouts generated
- AI fallback chain hit Gemini in all cases
- RLS verified — non-member returns empty array
- AI quota RPC verified

## Backlog / P1 (Post-MVP)
- Email verification hard-gate before workspace creation
- CSV upload size + virus-scan placeholder
- Per-route rate limiting (slowapi) on auth endpoints
- Admin dashboard for founder/support visibility
- Weekly digest email
- Slack/Intercom/Jira integrations (real, not just stubs)
- Compare mode polish on opportunities
- Multi-workspace per user (currently 1 active workspace context)
- SSO for Scale tier
- Mobile responsiveness pass

## Backlog / P2
- Embeddings/semantic search
- Predictive AI for churn risk
- QBR pack export (PDF)
- Competitor intelligence
- Public changelog automation

## Files Map
- `/app/frontend/` — Vite app
- `/app/backend/server.py` — FastAPI (single file, 700 LOC)
- `/app/backend/.env` — all secrets
- `/app/supabase/schema.sql` — main schema
- `/app/supabase/02_hardening.sql` — quota/audit/role helpers
- `/app/memory/test_credentials.md` — testing credentials

## Next Action Items
1. **Configure Dodo webhook URL in Dodo dashboard** → `https://b70ca5dc-b819-4ce8-b854-d49778beb5c5.preview.emergentagent.com/api/billing/webhook`
2. **Configure cron job** to hit `/api/cron/send-launch-reminders` daily with header `x-cron-secret: <DODO_WEBHOOK_SECRET>`
3. **Add Site URL + Redirect URLs in Supabase Auth** → `https://b70ca5dc-b819-4ce8-b854-d49778beb5c5.preview.emergentagent.com` + `/onboarding/step-1` + `/reset-password`
4. **Verify Resend domain** if not already done (`astrixai.app`)

## Definition of Done — ✅ MET
- [x] Signup
- [x] Workspace creation
- [x] CSV upload signals + accounts
- [x] Group signals into problems
- [x] Ranked opportunities
- [x] Create decision
- [x] Generate decision memo
- [x] Create launch
- [x] Day 7 + Day 30 reviews
- [x] Final verdict
- [x] Proof summary on dashboard
