# CRM Backend API

Professional FastAPI backend for the CRM application.

## Setup

### Prerequisites
- Python 3.14.4 (see `.python-version` in the repo root)
- PostgreSQL (or SQLite for development)

### Installation

1. Create virtual environment:
```bash
python -m venv venv
```

2. Activate virtual environment:
```bash
# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment:
```bash
cp .env.example .env
# Edit .env with your configuration
```

5. Initialize database:
```bash
# Run migrations
alembic upgrade head
```

New migrations: `alembic revision --autogenerate` also picks up old, known differences between the models and the database (listed in `tests/ops/schema_drift_baseline.txt`), including indexes it would drop. Keep only the operations for your change. CI fails if the models and migrations stop matching that list.

6. Run development server (local only; do not use in production):
```bash
uvicorn app.main:app --reload --port 8000
```

---

## Production (AWS EC2)

For production deployment, use **Gunicorn** with Uvicorn workers. Do **not** run raw `uvicorn` or use `--reload` in production.

Example production command:

```bash
gunicorn app.main:app \
    -w 3 \
    -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:8000
```

- **`-w 3`** — Number of worker processes (adjust based on CPU cores).
- **`uvicorn.workers.UvicornWorker`** — Provides async ASGI support.
- **`-b 0.0.0.0:8000`** — Bind on all interfaces (required when behind Nginx or a load balancer).
- **Do not use `--reload`** in production.

Ensure `DATABASE_URL`, `SECRET_KEY`, and `CORS_ORIGINS` are set in the environment (e.g. via `.env` in `WorkingDirectory` or systemd `Environment`/`EnvironmentFile`). The app loads these from `app.config`; no hardcoded DB or secrets.

Use **systemd** or another process manager to keep the service running and restart on failure.

### Optional: systemd example

Example unit file: `/etc/systemd/system/crm.service`

```ini
[Unit]
Description=CRM Backend
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/backend
ExecStart=/home/ubuntu/venv/bin/gunicorn app.main:app -w 3 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
Restart=always

[Install]
WantedBy=multi-user.target
```

- This is an example only; paths must match your EC2 deployment.
- Adjust `User`, `WorkingDirectory`, and the virtualenv path (e.g. `/home/ubuntu/venv/bin/gunicorn`) to your setup.
- Enable and start: `sudo systemctl enable crm && sudo systemctl start crm`

---

## API Documentation

Once running, access:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Project Structure

```
backend/
├── app/
│   ├── main.py              # FastAPI app
│   ├── config.py            # Configuration
│   ├── database.py          # Database connection
│   ├── models/              # SQLAlchemy models
│   ├── schemas/             # Pydantic schemas
│   ├── routers/             # API endpoints
│   ├── services/            # Business logic
│   └── utils/               # Utilities
├── alembic/                 # Database migrations
├── tests/                   # Tests
└── requirements.txt         # Dependencies
```

## Available Endpoints

### Authentication
- `POST /api/auth/signup` - Register new user
- `POST /api/auth/login` - Login and get token
- `POST /api/auth/refresh` - Refresh access token

### Users
- `GET /api/users/me` - Get current user
- `PUT /api/users/me` - Update current user
- `GET /api/users` - List users (admin only)

### Leads
- `GET /api/leads` - List leads
- `POST /api/leads` - Create lead
- `GET /api/leads/{id}` - Get lead
- `PUT /api/leads/{id}` - Update lead
- `DELETE /api/leads/{id}` - Delete lead

### Tasks
- `GET /api/tasks` - List tasks
- `POST /api/tasks` - Create task
- `GET /api/tasks/{id}` - Get task
- `PUT /api/tasks/{id}` - Update task
- `DELETE /api/tasks/{id}` - Delete task

## Testing

Run tests:
```bash
pytest
```

## Database

Default: PostgreSQL

For development, you can use SQLite by setting:
```
DATABASE_URL=sqlite:///./crm.db
```
