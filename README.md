# Perioxia CRM

A production-grade, multi-tenant CRM system built for service businesses, agencies, and teams. The platform features robust company-level isolation, a sophisticated role-based access control (RBAC) system, financial ledgers, and a comprehensive platform administration layer.

## 🚀 Key Features

- **Kanban Pipeline Board**: Drag-and-drop lead management for sales teams.
- **Lead Activity Timeline**: Full audit history of interactions, status changes, and notes for every lead.
- **Bulk CSV Operations**: High-performance bulk lead import and export for managers and MDs.
- **Document Management**: Secure file storage for leads and clients with ownership-based access.
- **Financial Suite**: Professional invoice generation, sales approvals, and automated financial ledgers.
- **Smart Notifications**: Real-time in-app bell notifications for assignments, status updates, and administrative approvals.
- **Custom Report Builder**: Dynamic data visualization for Managing Directors with team-level performance metrics.

## 🛠️ Tech Stack

### Frontend
- **Next.js 15** (React 18, App Router)
- **Tailwind CSS** (Modern utility-first styling)
- **Axios** (API client)
- **Recharts** (Dynamic data visualization)
- **Lucide React** (Consistent iconography)

### Backend
- **Python / FastAPI** (High-performance async REST API)
- **SQLAlchemy 2.x** (Modern ORM with typed mapping)
- **Alembic** (Reliable database migrations)
- **PostgreSQL** (Primary relational database)
- **JWT** (Stateless authentication via python-jose)
- **Pydantic v2** (Strict request/response validation)

## 🏗️ Technical Architecture

### Domain-Driven Design (DDD)
The backend is organized into functional domains to maximize maintainability and prevent circular dependencies:
- **`core/`**: Authentication, User Management, Audit Logs, and Company Infrastructure.
- **`sales/`**: Leads, Clients, Tasks, Follow-ups, and Kanban logic.
- **`finance/`**: Invoices, Sales Approvals, and Ledgers.
- **`hr/`**: Employee Leave and Transfer requests.
- **`ops/`**: Document Management, Bulk Processing, and System Monitoring.

### Multi-Tenancy & Security
- **Strict Row-Level Isolation**: Every entity is scoped by `company_id`.
- **`apply_company_scope` Helper**: A centralized security utility that automatically injects company filters into SQL queries, preventing cross-tenant data leaks.
- **Secure ID Policy**: Cross-tenant resource requests return 404 (Not Found) instead of 403 (Forbidden) to prevent resource enumeration attacks.

## 👥 Roles & Permissions

| Role | Responsibility | Key Features |
|:---|:---|:---|
| **Sales Executive** | Daily lead conversion | Access to assigned leads, tasks, and follow-ups. |
| **Team Manager** | Oversight & Quality | Full team visibility, task assignment, and performance reports. |
| **Managing Director** | Strategic Analysis | Company-wide analytics, revenue reports, and custom builders. |
| **Purchase Head** | Financial Operations | Sales approval workflow and invoice management. |
| **Company Admin** | Organization Setup | Team management, user invites, and company settings. |
| **Platform Admin** | System Oversight | Tenant approval, system logs, and global monitoring. |

## 📁 Project Structure

```text
CRM-/                          # Monorepo root (npm workspaces; see package.json)
├── package.json               # Root scripts: npm run dev / build → frontend workspace
├── scripts/                   # Optional repo utilities (e.g. run-all-tests.ps1)
├── backend/                   # FastAPI application
│   ├── app/
│   │   ├── models/            # SQLAlchemy models (domain folders: core, sales, finance, hr, ops)
│   │   ├── routers/           # API routes (domain folders + main.py wiring)
│   │   ├── schemas/           # Pydantic request/response models
│   │   ├── utils/             # Security, email, audit, helpers
│   │   ├── middleware/        # HTTP middleware
│   │   └── database.py        # Engine & session
│   ├── alembic/               # Migrations (alembic upgrade head)
│   └── tests/                 # Pytest
├── frontend/                  # Next.js 15 (App Router)
│   ├── app/                   # Routes & layouts by role (/sales, /manager, /md, /platform, …)
│   ├── components/            # Shared UI (charts, finance, leads, platform, …)
│   ├── contexts/              # Auth, notifications
│   ├── services/              # API client (axios)
│   └── jsconfig.json          # Path alias @/* → ./*
└── flutter_app/               # Flutter mobile client (separate from web)
```

Schema is defined and evolved via **Alembic** under `backend/alembic/` (no separate `database/` folder in this repo).

## ⚙️ Configuration (.env)

| Variable | Description | Default |
|:---|:---|:---|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://...` |
| `SECRET_KEY` | JWT signing key | *Required* |
| `ALGORITHM` | Token hashing algorithm | `HS256` |
| `CORS_ORIGINS` | Allowed frontend domains | `http://localhost:3000` |
| `SMTP_HOST` | SMTP host for email | `smtp.gmail.com` |
| `SMTP_PORT` | SMTP port | `587` |
| `SMTP_USER` | SMTP username | *(none)* |
| `SMTP_PASSWORD` | SMTP password / app password | *(none)* |
| `SMTP_FROM_EMAIL` | From address (optional; falls back to `SMTP_USER`) | *(none)* |
| `SMTP_TLS` | Use STARTTLS | `true` |

## 🏁 Getting Started

### Prerequisites
Production, CI and local development use the same versions:
- Python 3.14.4 (`.python-version`)
- Node.js 24.22.2 (`.nvmrc`)
- PostgreSQL 17.11
- Redis 8.0.5 (team chat)

### 1. Backend Setup
```bash
cd backend
python -m venv venv
source venv/bin/activate  # (or venv\Scripts\activate on Windows)
pip install -r requirements.txt

# Environment Configuration
cp .env.example .env
# python -c "import secrets; print(secrets.token_urlsafe(48))"

# Migrations & Database
createdb local_service_crm
alembic upgrade head

# Run API (dev)
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend Setup

From the **repository root** (recommended; uses the workspace):

```bash
npm install
npm run dev
```

Or from `frontend/` directly:

```bash
cd frontend
npm install
npm run dev
```

## 🚀 Deployment

**Production Recommendation:**
- **Backend**: Use Gunicorn with Uvicorn workers behind an Nginx reverse proxy.
- **Frontend**: Deploy via Vercel or a standalone Node.js server.
- **Database**: Managed PostgreSQL (e.g., AWS RDS or Supabase).

```bash
# Production execution example
gunicorn -w 4 -k uvicorn.workers.UvicornWorker app.main:app
```

## 📚 API Documentation

FastAPI provides interactive Swagger documentation. Start the backend and visit:
**`http://localhost:8000/docs`**

## 🧪 E2E (Playwright)

This repo’s Playwright config starts servers on:
- Frontend: **`http://127.0.0.1:3001`**
- Backend: **`http://127.0.0.1:8001`**

Run:
```bash
cd frontend
npm run test:e2e
```

## 📜 License

MIT License - Built for professional SaaS deployments.
