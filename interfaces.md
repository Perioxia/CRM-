# Interfaces and URLs

Every page in the app, grouped by interface. Base URL is the local frontend: **http://localhost:3000** (if port 3000 is busy, Next.js uses 3001 — swap the port).

Start everything with `make dev-all` from the repo root.

| | |
|---|---|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| Backend API docs (Swagger) | http://localhost:8000/docs |
| Live frontend | https://crm.perioxia.com |

`[id]`, `[slug]`, `[token]` etc. are placeholders — open the list page and click a row to get a real one.

## Test credentials

Password for every account: `ManualTest#2026`

Re-seed (safe to re-run): `cd backend && ./venv/bin/python scripts/seed_manual_test_users.py`

| Interface | Email | Login page |
|---|---|---|
| Platform admin (all companies) | `qa-platform-admin@manualtest.local` | http://localhost:3000/platform/login |
| Company admin | `qa-admin@manualtest.local` | http://localhost:3000/login |
| Managing Director | `qa-md@manualtest.local` | http://localhost:3000/login |
| Manager | `qa-manager@manualtest.local` | http://localhost:3000/login |
| Sales | `qa-sales@manualtest.local` | http://localhost:3000/login |
| Purchase | `qa-purchase@manualtest.local` | http://localhost:3000/login |

Platform admin only works on `/platform/login`; company roles only work on `/login`.

---

## 1. Platform admin (overall admin, across all companies)

- Login — http://localhost:3000/platform/login
- Dashboard — http://localhost:3000/platform/dashboard
- Pending signups — http://localhost:3000/platform/requests
- Companies — http://localhost:3000/platform/companies
- Company detail — http://localhost:3000/platform/companies/[id]
- Audit log — http://localhost:3000/platform/logs
- Plans — http://localhost:3000/platform/plans
- Platform session — http://localhost:3000/platform/session

## 2. Company admin

- Dashboard — http://localhost:3000/admin/dashboard
- User management — http://localhost:3000/admin/users
- User detail — http://localhost:3000/admin/users/[userId]
- Team management — http://localhost:3000/admin/teams
- Hierarchy — http://localhost:3000/admin/hierarchy
- Approvals — http://localhost:3000/admin/approvals
- Audit logs — http://localhost:3000/admin/audit
- Products — http://localhost:3000/admin/products
- AI assistant — http://localhost:3000/admin/assistant
- Admin settings — http://localhost:3000/admin/settings

## 3. Managing Director (MD)

- Dashboard — http://localhost:3000/md/dashboard
- Leads — http://localhost:3000/md/leads
- Lead detail — http://localhost:3000/md/leads/[id]
- Unassigned leads — http://localhost:3000/md/leads/unassigned
- Leads trash — http://localhost:3000/md/leads/trash
- Clients — http://localhost:3000/md/clients
- Client detail — http://localhost:3000/md/clients/[id]
- Accounts — http://localhost:3000/md/accounts
- Account detail — http://localhost:3000/md/accounts/[id]
- Deals — http://localhost:3000/md/deals
- Deal detail — http://localhost:3000/md/deals/[id]
- Appointments — http://localhost:3000/md/appointments
- Conversations — http://localhost:3000/md/conversations
- Invoices — http://localhost:3000/md/invoices
- Invoice detail — http://localhost:3000/md/invoices/[invoiceId]
- Sales — http://localhost:3000/md/sales
- Revenue — http://localhost:3000/md/revenue
- Performance — http://localhost:3000/md/performance
- Points — http://localhost:3000/md/points
- Teams — http://localhost:3000/md/teams
- Employee lookup — http://localhost:3000/md/employee-lookup
- Employee detail — http://localhost:3000/md/employee-lookup/[id]
- Monitoring — http://localhost:3000/md/monitoring
- Products — http://localhost:3000/md/products
- Stock — http://localhost:3000/md/stock
- Reports — http://localhost:3000/md/reports
- AI assistant — http://localhost:3000/md/assistant

## 4. Manager

- Dashboard — http://localhost:3000/manager/dashboard
- Team — http://localhost:3000/manager/team
- Team member detail — http://localhost:3000/manager/team/[id]
- Tasks — http://localhost:3000/manager/tasks
- Leads — http://localhost:3000/manager/leads
- Lead detail — http://localhost:3000/manager/leads/[id]
- Unassigned pool — http://localhost:3000/manager/leads/unassigned
- Leads trash — http://localhost:3000/manager/leads/trash
- Clients — http://localhost:3000/manager/clients
- Client detail — http://localhost:3000/manager/clients/[id]
- Accounts — http://localhost:3000/manager/accounts
- Account detail — http://localhost:3000/manager/accounts/[id]
- Deals — http://localhost:3000/manager/deals
- Deal detail — http://localhost:3000/manager/deals/[id]
- Appointments — http://localhost:3000/manager/appointments
- Conversations — http://localhost:3000/manager/conversations
- Invoices — http://localhost:3000/manager/invoices
- Invoice detail — http://localhost:3000/manager/invoices/[invoiceId]
- Finance ledger — http://localhost:3000/manager/finance/[ledger]
- Products — http://localhost:3000/manager/products
- Stock — http://localhost:3000/manager/stock
- Reports — http://localhost:3000/manager/reports
- AI assistant — http://localhost:3000/manager/assistant
- Settings — http://localhost:3000/manager/settings

## 5. Sales

- Dashboard — http://localhost:3000/sales/dashboard
- Tasks — http://localhost:3000/sales/tasks
- Follow-ups — http://localhost:3000/sales/follow-ups
- Leads — http://localhost:3000/sales/leads
- Lead detail — http://localhost:3000/sales/leads/[id]
- Unassigned leads (view only) — http://localhost:3000/sales/leads/unassigned
- Leads trash — http://localhost:3000/sales/leads/trash
- Clients — http://localhost:3000/sales/clients
- Client detail — http://localhost:3000/sales/clients/[id]
- Accounts — http://localhost:3000/sales/accounts
- Account detail — http://localhost:3000/sales/accounts/[id]
- Deals — http://localhost:3000/sales/deals
- Deal detail — http://localhost:3000/sales/deals/[id]
- Appointments — http://localhost:3000/sales/appointments
- Conversations — http://localhost:3000/sales/conversations
- Quotes — http://localhost:3000/sales/quotes
- New quote — http://localhost:3000/sales/quotes/new
- Orders — http://localhost:3000/sales/orders
- Invoice detail — http://localhost:3000/sales/invoices/[invoiceId]
- Products — http://localhost:3000/sales/products
- Stock — http://localhost:3000/sales/stock
- Performance — http://localhost:3000/sales/performance
- Reports — http://localhost:3000/sales/reports
- AI assistant — http://localhost:3000/sales/assistant
- Settings — http://localhost:3000/sales/settings

## 6. Purchase

- Dashboard — http://localhost:3000/purchase/dashboard
- Sales — http://localhost:3000/purchase/sales
- Sale detail — http://localhost:3000/purchase/sales/[saleId]
- Invoices — http://localhost:3000/purchase/invoices
- Invoice detail — http://localhost:3000/purchase/invoices/[invoiceId]
- Products — http://localhost:3000/purchase/products
- Stock — http://localhost:3000/purchase/stock
- Monitoring — http://localhost:3000/purchase/monitoring
- AI assistant — http://localhost:3000/purchase/assistant

---

## Shared pages (any logged-in company user; some are admin/MD only)

- Profile — http://localhost:3000/profile
- Team chat — http://localhost:3000/chat
- Cases — http://localhost:3000/cases
- Campaigns — http://localhost:3000/campaigns
- Mass email — http://localhost:3000/mass-email
- Saved reports — http://localhost:3000/reports
- Forecast — http://localhost:3000/reports/forecast
- Finance — http://localhost:3000/finance
- Finance ledger — http://localhost:3000/finance/[ledger]
- Financial ledgers — http://localhost:3000/financial-ledgers
- Financial ledger detail — http://localhost:3000/financial-ledgers/[ledgerName]
- Custom modules — http://localhost:3000/modules
- Custom module detail — http://localhost:3000/modules/[slug]
- Report a bug — http://localhost:3000/report-bug

### Company settings

- Settings home — http://localhost:3000/settings
- Accounting — http://localhost:3000/settings/accounting
- API keys — http://localhost:3000/settings/api-keys
- Approvals — http://localhost:3000/settings/approvals
- Calendar — http://localhost:3000/settings/calendar
- E-invoice — http://localhost:3000/settings/einvoice
- Email — http://localhost:3000/settings/email
- Email templates — http://localhost:3000/settings/email-templates
- Lead sheet — http://localhost:3000/settings/lead-sheet
- Leave — http://localhost:3000/settings/leave
- Marketplace — http://localhost:3000/settings/marketplace
- Modules — http://localhost:3000/settings/modules
- Predictions — http://localhost:3000/settings/predictions
- Price books — http://localhost:3000/settings/price-books
- Privacy — http://localhost:3000/settings/privacy
- Quote plans — http://localhost:3000/settings/quote-plans
- Sandbox — http://localhost:3000/settings/sandbox
- Scoring — http://localhost:3000/settings/scoring
- Security — http://localhost:3000/settings/security
- SSO — http://localhost:3000/settings/sso
- Telephony — http://localhost:3000/settings/telephony
- Territories — http://localhost:3000/settings/territories
- Webhooks — http://localhost:3000/settings/webhooks
- WhatsApp — http://localhost:3000/settings/whatsapp

---

## Public pages (no login)

### Auth

- Home — http://localhost:3000/
- Login (company users) — http://localhost:3000/login
- Signup (new company) — http://localhost:3000/signup
- Forgot password — http://localhost:3000/forgot-password
- Accept invite — http://localhost:3000/accept-invite/[token]
- Privacy policy — http://localhost:3000/privacy

### Customer-facing links (shared by a company)

- Booking page — http://localhost:3000/book/[slug]
- Lead form — http://localhost:3000/f/[slug]
- Web-to-case (support) form — http://localhost:3000/c/[slug]
- Website widget — http://localhost:3000/w/[slug]
- Invoice view — http://localhost:3000/p/invoice/[token]
- Quote view — http://localhost:3000/p/quote/[token]
- Payment — http://localhost:3000/p/pay/[token]
