# Perioxia CRM — Handover Context

Written 5 Oct 2026 for Sourav, who is taking over the project. Everything below was checked against the code, git history, and a test run on this date unless it is marked *(unverified)*.

Read this file first, then [`aim-and-trajectory.md`](./aim-and-trajectory.md) for the why and where it's heading.

---

## 1. TL;DR

- **What it is:** a multi-tenant SaaS CRM for small Indian service businesses (agencies, contractors, clinics, shops). FastAPI + PostgreSQL backend, Next.js 15 web app, a Flutter mobile app.
- **Where it stands:** feature-wise, the roadmap is fully built in code: Phases 0–7 (trust, billing, sales loop, Zoho-parity features, trial defense) plus the September "SMB wedge" (Unassigned Pool, Appointments, WhatsApp Conversations, Day-0 onboarding, daily digests, mobile screens). Nothing is half-built on the branch.
- **What it is not yet:** proven in production. Most integrations (Razorpay, Gupshup WhatsApp, Exotel, Gmail/Outlook, Tally, GST e-invoice) are coded and tested against fakes, but **none has been verified end-to-end with live credentials**. There are no paying customers or design partners on record.
- **Tests (run 5 Oct 2026):** backend **891 passed, 2 failed**. Both failures are stale tests, not product bugs (see §7). Frontend unit tests: 27/27 pass. `next build` and Playwright were **not** run for this handover.
- **Immediate to-dos:** push the 3 unpushed commits, fix the 2 stale tests, add a real cron for the scheduled jobs, then verify each integration on production with real credentials (§10).

---

## 2. Repository state right now

| Item | State |
|---|---|
| Remote | `https://github.com/mayank-sharma-pant/CRM-.git` |
| Current branch | `feat/smb-wedge` |
| vs `origin/main` | **3 commits ahead, 0 behind; not pushed.** `b8a438f` (SMB onboarding + daily digest), `f0efd7e` (Google Sheet lead sync + Flutter Appointments/Conversations), `01f7472` (plan checkboxes). |
| Uncommitted changes | `frontend/app/globals.css`, `frontend/components/Layout.jsx`: a UI polish pass (`app-desk` background class plus a page-enter animation keyed on the route). Small and cosmetic. Either commit it or drop it. |
| Other local branches | `feat/ops-pages`, `feat/phase7-trial-defense`, `fix/deal-timeline-and-next-activity-tests`: all already merged into `origin/main`. Safe to delete. |
| Worktree | `.worktrees/feat-phase7-trial-defense/`: an old checkout that still holds the original `docs/` folder (roadmap, plans, specs). Gitignored. |
| Live site | `https://crm.perioxia.com` (from `testing.md`). *(Unverified: which commit is deployed. The 3 unpushed commits and migration `039_lead_sheet` are almost certainly not live.)* |

**Junk in the repo you can delete:** `frontend/tmp_refactor_charts.js`. `backend/crm.db.bak.1788877820` is a **SQLite DB backup committed to git**. Check whether it holds real data. If it does, remove it from history.

`frontend/.env` is tracked but holds no secrets (only `BACKEND_URL=http://127.0.0.1:8000`).

---

## 3. Architecture

### 3.1 Stack

| Layer | Tech | Where |
|---|---|---|
| Backend | Python, FastAPI 0.132, SQLAlchemy 2, Pydantic v2, Alembic, psycopg 3, httpx | `backend/` |
| DB | PostgreSQL in prod; SQLite (`backend/crm.db`) is the local/test default | `DATABASE_URL` |
| Web | Next.js 15.5 App Router, React 18, Tailwind 3, axios, recharts, framer-motion. Plain JSX (no TypeScript, except `src/middleware.ts`). | `frontend/` |
| Mobile | Flutter (Riverpod, Dio), package `perioxia_crm` | `flutter_app/` |
| Monorepo | Root `package.json` uses npm workspaces (frontend only) | root |

### 3.2 Backend layout

```
backend/app/
  main.py          # wires ~78 routers, all under /api/...
  config.py        # all env settings (pydantic-settings)
  database.py      # engine/session
  tenancy.py       # Postgres RLS: SET LOCAL app.company_id per transaction
  schema_sync.py   # idempotent create_all + MISSING_COLUMNS + RLS policies
  models/          # core, sales, finance, hr, ops, billing
  routers/         # auth, admin, ai, billing, company, finance, management, ops, public, sales
  services/        # business logic (sales/, billing/, accounting/, finance/, scoring/, predictions/ ...)
  utils/           # dependencies (auth + apply_company_scope), audit, security, email, totp
backend/alembic/versions/   # head = 039_lead_sheet
backend/tests/              # ~893 pytest tests
backend/scripts/            # seed_manual_test_users.py, bootstrap_platform_admin.py, repair_orphan_leads.py ...
```

Interactive API docs: `http://localhost:8000/docs` when the backend runs. That is the authoritative endpoint list.

### 3.3 Multi-tenancy (the most important invariant)

- Every tenant row has `company_id`.
- Application layer: `apply_company_scope` / `ensure_company_access` in `app/utils/dependencies.py`. Every query on tenant data goes through these.
- Database layer: Postgres RLS (`app/tenancy.py`, policies installed by `schema_sync`). On SQLite it is a no-op, so **CI never exercises RLS**.
- Cross-tenant access returns **404, not 403**, so IDs can't be enumerated.
- `backend/tests/tenancy/` holds the cross-tenant matrix. Any new resource needs a cross-tenant test with a positive control.

### 3.4 Roles

| Role | Home | Scope |
|---|---|---|
| `sales` | `/sales/dashboard` | own leads + open leads on active team |
| `manager` | `/manager/dashboard` | active team (one manager per team) |
| `md` (Managing Director) | `/md/dashboard` | whole company |
| `purchase` | `/purchase/dashboard` | sales-order approval, invoices, stock |
| `admin` (company admin) | `/admin/dashboard` | users, teams, settings |
| platform admin | `/platform/*` (own login at `/platform/login`) | all tenants: approvals, plans, logs |

The frontend sends `X-Team-Id` (via `services/api.js`) for the active team. Manager and sales scoping depends on it.

### 3.5 Frontend conventions

- Role-prefixed routes (`/sales/*`, `/manager/*`, `/md/*`, ...) are **thin re-exports** of shared components in `frontend/components/` (for example `components/leads/LeadsIndexPage.jsx`). Build the component once and re-export it per role.
- Path helpers: `frontend/lib/objectPaths.cjs`. Sidebar nav per role: `frontend/components/Sidebar.jsx` (`ROLE_NAVIGATION`).
- Shared (non-role) routes: `/settings/*`, `/reports`, `/campaigns`, `/cases`, `/modules`, `/mass-email`, `/finance`.
- Public routes: `/`, `/login`, `/signup`, `/privacy`, `/f/[slug]` (lead form), `/book/[slug]` (booking), `/c/[slug]` (web-to-case), `/w/[slug]` (widget), `/p/quote|invoice|pay/[token]` (customer portal). A new public route must be allow-listed in **four** places: `src/middleware.ts`, `RouteGuard.jsx`, `Layout.jsx`, and the 401 interceptor in `services/api.js`.
- Design system is called "Ledger Desk": Tailwind tokens `bg-page`, `bg-surface`, `border-border`, `text-primary`, `text-muted`, `btn btn-primary`. Don't introduce a second style.
- i18n: English + Hindi only. Add every new visible label to `frontend/lib/i18n/hi.cjs`, keyed by the English string. A missing key falls back to English.
- API proxy: `next.config.mjs` rewrites `/api/*` to `BACKEND_URL`, so the browser only calls its own origin (no CORS in prod when deployed this way).

---

## 4. Running it locally

```bash
# backend
cd backend
python -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env          # set SECRET_KEY; DATABASE_URL defaults to sqlite:///./crm.db
./venv/bin/alembic upgrade head   # and/or: ./venv/bin/python create_missing_tables.py
./venv/bin/python scripts/seed_manual_test_users.py

# both servers together, from repo root
npm install
npm run dev-all               # backend :8000, frontend :3000
```

**Test accounts:** `testing.md` lists the seeded QA users (`qa-admin@`, `qa-manager@`, `qa-sales@`, `qa-md@`, `qa-purchase@`, `qa-platform-admin@manualtest.local`) and the full manual walkthrough by role. Passwords are in `passwords.md`, which is **gitignored**, so a fresh clone won't have it. Get it from the previous owner directly.

---

## 5. Tests

| Suite | Command | Result on 5 Oct 2026 |
|---|---|---|
| Backend | `cd backend && ./venv/bin/python -m pytest -q` | 891 passed, 2 failed (~2 min) |
| Frontend unit | `cd frontend && npm run test:paths` (also `test:landing`, `test:brand`, `test:store`, `test:i18n`, plus `node --test lib/leadCsv.test.cjs`) | 27/27 pass |
| E2E | `cd frontend && npm run test:e2e` (Playwright starts its own servers on :3001/:8001) | not run |
| Flutter | `cd flutter_app && flutter test` | not run (no Flutter SDK on this machine; it has never been run in CI) |
| Manual | `testing.md` | role-by-role checklist |

The two backend failures (both stale tests, safe to fix):
1. `tests/ops/test_alembic_heads.py::test_alembic_has_exactly_one_head` asserts the head is `038_quote_plans`. The real head is `039_lead_sheet`. Update the expected value.
2. `tests/ops/test_imports_api.py::test_import_leads_internal_error_returns_sanitized_500` monkeypatches `log_activity` in the import router, but the lead-import path no longer calls it (only the client and deal imports do). The forced error never fires, so the route returns 200. Rewrite the test to patch something the lead path actually calls, such as `insert_new_leads`.

There is no CI pipeline in the repo.

---

## 6. Deploying

*(Partly unverified: the hosting setup isn't documented in one place.)* The evidence points to:
- **Frontend on Vercel.** `frontend/.env` comments say to set `BACKEND_URL` in Vercel, and `/api/*` is rewritten to it.
- **Backend on a Procfile host** (Railway, Render, or Heroku-style; `backend/Procfile` runs gunicorn with uvicorn workers on `$PORT`). `backend/README.md` also describes an EC2 + systemd option.
- **Postgres on a managed provider** (`.env.example` mentions Neon/Supabase).

Every deploy that adds schema:

```bash
alembic upgrade head            # head = 039_lead_sheet
python create_missing_tables.py # idempotent; also seeds plans and installs RLS policies on Postgres
```

Both are idempotent. Many past phases added columns through `schema_sync.MISSING_COLUMNS` and not through a dedicated migration, so **run both**.

Required production env (see `backend/app/config.py` for the full list):
- `ENVIRONMENT=production`, `DATABASE_URL`, `SECRET_KEY` (boot refuses the placeholder), `CORS_ORIGINS`, `FRONTEND_URL`, `PUBLIC_API_URL` (must be the public backend origin, or OAuth callbacks, webhooks, and email tracking won't work), `TRUSTED_HOSTS`, `AUTH_COOKIE_*`, `SMTP_*`.
- Feature keys: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `TRIAL_DAYS`, `GOOGLE_OAUTH_CLIENT_ID/SECRET`, `MICROSOFT_OAUTH_CLIENT_ID/SECRET/TENANT`, `OPENAI_KEY`/`OPENAI_MODEL` or `GEMINI_API_KEY`/`GEMINI_MODEL`.
- **`backend/.env.example` is missing** the Razorpay, OAuth, Gemini, and `TRIAL_DAYS` keys. Add them so the next deploy doesn't miss them.

Known production gotchas from history: CSV import broke on prod when a preview route was missing (fixed in `ad13616` / `47b83c3`). Users with no team couldn't import (fixed by auto-bootstrapping a team on `/teams/mine`, `dc40aa4`). `backend/scripts/repair_orphan_leads.py` exists to repair leads left without a team.

---

## 7. What's done

Git history goes back to Aug 2026 (330 commits). The original roadmap (`PRODUCT_ROADMAP.md`) and phase log (`IMPLEMENTATION_PLAN.md`) were deleted from the branch in commit `b5167e9` but are recoverable:

```bash
git show b5167e9^:docs/IMPLEMENTATION_PLAN.md   # full phase-by-phase log, residuals, deploy notes
git show b5167e9^:docs/PRODUCT_ROADMAP.md       # strategy, competitor analysis, decision log
git show 3127d08^:docs/superpowers/specs/       # per-feature design specs (list, then show a file)
```

Every phase item there has a **Residuals** line listing exactly what was left out. Read it before you extend a feature.

### 7.1 Roadmap phases (Aug 2026) — all "done in code"

| Phase | What shipped |
|---|---|
| 0 Trust | Cross-tenant test matrix, Postgres RLS, prod boot refuses default `SECRET_KEY`, `/health` pings the DB, rotating refresh tokens + logout revoke, AI action audit log + dry-run |
| 1 Billing | `plans` / `subscriptions` tables, Razorpay adapter (+ Null provider for tests), idempotent signed webhook, 14-day self-serve trial, seat/team/storage limits (HTTP 402) |
| 2 Sales loop | Deals with configurable pipelines/stages, public web form → lead, custom fields, quote → invoice → payment link, workflow engine v0, cadences, email from CRM, tags, recycle bin, duplicate merge, reminders |
| 3 Zoho Standard | TOTP 2FA + recovery codes, meetings + call log, multiple pipelines, saved reports + dashboard, public API keys (`/api/v1`, `crm_live_` keys, daily quota), CSV import mapper with duplicate preview, GST invoices (CGST/SGST/IGST), WhatsApp templates (Gupshup), Flutter sales path |
| 4 Professional | Products + tax, blueprint (required stages), customer portal, forecasting vs quota, territory assignment, sandbox tenant, Google/Microsoft SSO |
| 5 Add-ons | Data enrichment (stub), lead/deal scoring, predictive win-probability + churn, Tally/QuickBooks sync (stub), custom modules, first-party marketplace, email campaigns, cases/web-to-case, sandbox data clone, capped mass email |
| 6 Parity | Gmail/Outlook mailbox sync, calendar push, Exotel click-to-call, unified activity timeline, shared object UI, portal pay + quote accept, WhatsApp inbound + auto sequences, mobile 2FA, onboarding checklist, RLS, honest landing page, brand cleanup, accounts (B2B), saved filters, outbound webhooks, invoice PDF + IRN (stub), website widget, SAML, GDPR/DPDP export + erase, Alembic single head |
| 7 Trial defense | Email open/click tracking, public booking page + inbound calendar pull, store-listing assets + `/privacy`, Hindi UI on the sales loop, live Tally push, live NIC/GSP e-invoice, price books, deal next-activity nag + rotting, quote → sales order → invoice, deal/discount approvals, import undo + clients/deals CSV, deal and GST report types + scheduled email |

### 7.2 September 2026 (after the roadmap)

| When | Commit | What |
|---|---|---|
| 1 Sep | `defb978` | Landing page redesign |
| 21 Sep | `629250b` | **Unassigned Pool, Appointments, Conversations (WhatsApp inbox), email templates.** Spec: `plan-next.md` |
| 22 Sep | `d39842c` | Tenant-configurable quotations: quote plans, quotation wizard, quote PDF |
| 22–24 Sep | `384a745`, `7f0b349`, `753a2ab`, `c7e6618` | Product kinds (goods/services), UI polish across dashboards and platform pages, role walkthrough test (`tests/qa/test_role_walkthrough.py`), `testing.md` |
| 23 Sep | `ad13616`, `47b83c3`, `dc40aa4` | Production fixes: lead CSV import, team bootstrap |
| 25 Sep | `b8a438f`, `f0efd7e` (unpushed) | **SMB wedge phases 2–4** from `plan-todo.md`: Day-0 onboarding (invite teammate, connect WhatsApp, CSV, sample data with an unassigned lead + meeting), daily digest notifications, Flutter Appointments + Conversations + claim, Google Sheet lead pull (`/settings/lead-sheet`) |

`plan-next.md` and `plan-todo.md` are the most recent plans, and **every box in them is checked**. Treat them as a record, not a to-do list.

---

## 8. Integrations — status and what each needs to go live

"Code done" means implemented and tested against a fake. None of these has been verified against the real provider (the phase log states this for each one).

| Integration | Purpose | Config | Callback / webhook | Status |
|---|---|---|---|---|
| **Razorpay** | Our SaaS subscription + customer invoice payment links | env `RAZORPAY_*` | `POST {PUBLIC_API_URL}/api/billing/webhook` (events `payment_link.paid`, `payment.captured`) | Code done. Without keys, a stub pay page `/p/pay/{token}` is used. No dunning: cancel/past-due does not suspend the company. |
| **SMTP** | OTP, invites, CRM email fallback, reminders, scheduled reports | env `SMTP_*` | — | Works when set. Invites silently don't arrive without it (the UI still says "sent"). |
| **Gmail / Outlook mailbox** | Send from user's mailbox, log inbound/outbound on records | env `GOOGLE_OAUTH_*` / `MICROSOFT_OAUTH_*`; per-user connect at `/settings/email` | `{PUBLIC_API_URL}/api/mailbox/oauth/{google\|microsoft}/callback` | Code done. Sync is on demand (`POST /api/mailbox/sync`), no push. |
| **Google / Microsoft Calendar** | Push meetings; pull events | same OAuth clients; `/settings/calendar` | `{PUBLIC_API_URL}/api/calendar/oauth/{provider}/callback` | Code done. Inbound pull is manual (`POST /api/calendar/sync`). |
| **SSO login** | Login with Google/Microsoft (existing users only) | same OAuth clients | `{PUBLIC_API_URL}/api/auth/oauth/{provider}/callback` | Code done |
| **SAML** | Enterprise SSO | per company, in DB (`/settings/sso`) | ACS `{PUBLIC_API_URL}/api/auth/saml/{company_code}/acs` | Code done. Not exclusive-C14N XML-DSig; no SLO or JIT provisioning. |
| **WhatsApp (Gupshup)** | Template send, session replies, inbound inbox, cadence auto-send | per company API key + source number at `/settings/whatsapp` | `{PUBLIC_API_URL}/api/whatsapp/webhook?source=<number>` | Code done. Most important integration for the SMB wedge. No webhook HMAC, no media, no Interakt. |
| **Exotel** | Click-to-call + call logs | per company SID/token/ExoPhone at `/settings/telephony`; agents need `users.phone` | StatusCallback `{PUBLIC_API_URL}/api/telephony/exotel/webhook` | Code done. No Twilio, recordings, or inbound. |
| **Tally** | Push invoices as vouchers | per company `tally_url` at `/settings/accounting`; backend must reach the Tally gateway (usually the customer's LAN) | — | Live push coded, **never tested against a real Tally**. LAN reachability from a cloud backend is unsolved. |
| **QuickBooks** | — | — | — | Stub only (no OAuth) |
| **GST e-invoice (NIC/GSP)** | IRN generation | per company creds at `/settings/einvoice` | — | Live coded to the NIC contract, **never tested live**. Without creds, a hash stub IRN is used. |
| **Google Sheets** | Pull new leads from a sheet shared "anyone with link" | `/settings/lead-sheet` | — (pulled on Sync, on opening the leads list, and on the reminder run) | Code done, unpushed |
| **AI assistant** | Company assistant that can read and (audited) write CRM data | env `OPENAI_KEY` (preferred) or `GEMINI_API_KEY` | — | Code done. Default `OPENAI_MODEL=gpt-5.3-codex`; confirm the model name is valid for your key. |
| **Outbound webhooks** | Push `lead.created`, `deal.stage_changed`, `invoice.paid` to customer URLs | `/settings/webhooks` | HMAC header `X-Perioxia-Signature` | Code done. Retries only when `POST /api/webhooks/retry` is called. |
| **Public REST API** | Customer integrations | `/settings/api-keys` | `/api/v1/*` | Code done (leads, clients, deals, invoices) |
| **Data enrichment** | — | — | — | Stub only |
| **App stores** | Play / App Store listing | `flutter_app/store/STORE_RELEASE.md` | — | Listing copy, privacy page, and signing scaffold are ready. **Never built or submitted.** `android/` and `ios/` are not even generated yet (`flutter create` is a manual step). |

### 8.1 Scheduled jobs: nothing runs on its own

The backend has **no scheduler**. These endpoints do the periodic work, and something has to call them:

| Endpoint | Does | Auth |
|---|---|---|
| `POST /api/reminders/run` | Task/follow-up/deal-due reminders, WhatsApp cadence sends, **daily digests**, **Google Sheet pulls** | admin/MD JWT |
| `POST /api/settings/report-schedule/run` | Scheduled report emails | admin/MD JWT |
| `POST /api/webhooks/retry` | Retry failed outbound webhooks (caller's company only) | admin/MD JWT |
| `POST /api/scoring/recompute`, `POST /api/predictions/train` | Refresh scores / retrain models | admin/MD JWT |

Problem to solve: `reminders/run` and `report-schedule/run` loop over **all companies**, yet they're triggered by one company's admin login. On Postgres with RLS active, the run is probably limited to the caller's company; on SQLite it is global *(inferred from the code, not tested on Postgres)*. Fix: add a system-level trigger, for example a shared-secret cron header or a CLI command run by the host's cron, that runs without RLS scoping. Then schedule it every 15–60 minutes.

---

## 9. Agent / workflow conventions in this repo

- `AGENTS.md` and `CLAUDE.md` are rule files for AI coding agents (decide-and-proceed, no slop, verify before claiming). The previous owner built most of the code with AI agents (Cursor / Claude), following a spec → plan → subagent-implement → review loop. Artefacts are in `.superpowers/sdd/` and in the deleted `docs/superpowers/`.
- Plans use checkbox steps with failing test first, then the implementation (TDD).
- Commit messages from Sep onward are often meaningless ("asdf", "billa mc", "done"). Use `git show --stat <sha>` to see what a commit actually did; the table in §7.2 maps the important ones.

---

## 10. What's remaining (prioritised)

**A. Housekeeping (hours)**
1. Commit or discard the uncommitted UI change; push `feat/smb-wedge`; open a PR into `main`.
2. Fix the 2 stale backend tests (§5).
3. Add the missing keys to `backend/.env.example` (§6).
4. Delete junk files (§2). Check `backend/crm.db.bak.*` for real data.
5. Run `npm run build` and `npm run test:e2e` once to get a baseline.
6. Add CI (GitHub Actions: pytest + frontend unit tests + `next build`). Add a Postgres job so RLS is actually exercised.

**B. Make it actually run in production (days)**
1. Deploy the branch; run `alembic upgrade head` + `create_missing_tables.py` on prod.
2. Add the system cron trigger (§8.1) and schedule it.
3. Verify each integration with real credentials, in this order (most valuable to the SMB wedge first): SMTP → Gupshup WhatsApp (send + inbound webhook) → Razorpay (test mode: subscription + invoice payment link) → Google OAuth (login, mailbox, calendar) → Exotel → Tally / e-invoice only if a customer needs them.
4. Walk `testing.md` on production for every role.

**C. Known product gaps (pick by customer demand, not by list)**
- Billing: no dunning (expired paid plans don't suspend), `Subscription.current_period_end` not populated.
- WhatsApp: no webhook signature check, no media messages, no read/unread state.
- Appointments: list only (no calendar view), no availability/timezone on public booking, no reschedule/cancel for guests.
- Hindi covers only the sales-loop screens; settings and manager/MD-only screens are English.
- Mobile: never built or run on a device; store submission not done.
- Tally from a cloud backend needs a tunnel or agent on the customer LAN.
- Recurring computations (scoring, churn, rotting) are "as of last run" until the cron exists.
- AI assistant: multi-step actions aren't fully transactional (documented residual in Phase 0.5).

**D. Explicitly out of scope** (decided earlier, don't build unless the strategy changes): dealership features (Trust/SSN, BDC, sold cars), Meta Lead Ads, unified email+WhatsApp inbox, calendar toggle on Appointments, ⌘K palette, full helpdesk, marketing hub, Salesforce-style objects. See `plan-next.md` "Out of scope" and `aim-and-trajectory.md`.

---

## 11. Key files cheat-sheet

| Need | File |
|---|---|
| All env settings | `backend/app/config.py` |
| Router registry / URL prefixes | `backend/app/main.py` |
| Tenant scoping helpers | `backend/app/utils/dependencies.py` |
| RLS | `backend/app/tenancy.py`, `backend/app/schema_sync.py` |
| Lead scoping rules (sales/manager/md) | `backend/app/routers/sales/leads.py` (`list_leads`) |
| Reminders / digest / sheet cron | `backend/app/services/sales/reminders.py`, `daily_digest.py`, `lead_sheet.py` |
| Billing | `backend/app/services/billing/`, `backend/app/routers/billing/` |
| WhatsApp | `backend/app/routers/sales/whatsapp.py` |
| AI assistant | `backend/app/routers/ai/company_assistant.py`, `assistant_crm_actions.py` |
| Sidebar / nav per role | `frontend/components/Sidebar.jsx` |
| Route helpers | `frontend/lib/objectPaths.cjs` |
| Axios client + team header + 401 handling | `frontend/services/api.js` |
| Auth gate | `frontend/src/middleware.ts`, `frontend/components/RouteGuard.jsx` |
| Hindi strings | `frontend/lib/i18n/hi.cjs` |
| Manual QA script | `testing.md` |
| Latest plans (all done) | `plan-next.md`, `plan-todo.md` |
| Mobile release steps | `flutter_app/store/STORE_RELEASE.md` |
