# Deployment

Production is one Ubuntu 26.04 server behind crm.perioxia.com: nginx → Next.js website (`crm-frontend`, port 3000) → FastAPI backend (`crm-backend`, port 8000). The database is Neon PostgreSQL; Redis runs on the server.

## How a change goes live

1. Merge a pull request into `main`. The **Tests** workflow runs.
2. If Tests pass, the **Deploy** workflow starts and waits for approval: Actions → Deploy → *Review deployments* → *Approve*. Pick a quiet time; a backend restart drops chat and calls for a few seconds.
3. GitHub connects to the server with a key that can only run `deploy/gate.sh`, which runs `deploy/deploy.sh` from the commit being deployed.
4. `deploy.sh` checks the server, builds and test-runs the website in a separate folder (port 3001), switches the live site, checks again, and rolls back by itself if any check fails.

Only what changed restarts:

| Change | What happens | Downtime |
|---|---|---|
| `frontend/` | website rebuilt (about 5 minutes before the switch) | about 5 s |
| `backend/` code | backend restarted | about 8 s (chat and calls reconnect) |
| docs, tests, CI only | the server's checkout moves forward | none |

## What it never does

It never changes the database or installs Python packages. If an update needs either, the deploy stops before touching anything and says what is missing. Do that step by hand, then open the failed Deploy run and choose *Re-run jobs*.

**Database migration.** Migrations must work with the old code still running (add columns or tables; drop or rename only in a later release). Make a Neon backup branch first, then on the server:

```bash
cd /home/ubuntu/deploy && rm -rf migrate && mkdir migrate
git -C /home/ubuntu/CRM- fetch origin main && git -C /home/ubuntu/CRM- archive origin/main | tar -x -C migrate
cd migrate/backend && ln -s /home/ubuntu/CRM-/backend/.env .env
/home/ubuntu/CRM-/backend/venv/bin/alembic upgrade head
```

**Python packages.** Install exactly the versions the deploy listed:

```bash
/home/ubuntu/CRM-/backend/venv/bin/pip install 'name==version'
```

## Check-only run

Actions → Deploy → *Run workflow* on `main`, then approve. It builds and test-runs the latest `main`; nothing live changes.

## Rolling back

- A failed check after the switch rolls back automatically.
- Later: revert the commit in GitHub and deploy that, or follow `/home/ubuntu/deploy/cd-<time>-deploy/ROLLBACK.txt` (the previous website build is kept for the last two deploys).

## Logs

GitHub shows the run. The server keeps it in `/home/ubuntu/deploy/cd-<time>-<check|deploy>/run.log`, with the build logs beside it. The repository is public, so its Actions logs are public too; the scripts print only status lines.

## Turning it off

- Pause: Actions → Deploy → ⋯ → *Disable workflow*.
- Remove: delete the `crm-deploy-gate` line from `/home/ubuntu/.ssh/authorized_keys`.

## One-time setup (new server or new owner)

The scripts expect: the checkout at `/home/ubuntu/CRM-` on `main`, the `crm-frontend` and `crm-backend` services, the backend venv at `backend/venv`, Node at `/usr/bin/node`, and the `ubuntu` user with passwordless sudo.

1. On the server, install the gate: `install -D -m 755 deploy/gate.sh /home/ubuntu/bin/crm-deploy-gate`. Changes to `gate.sh` must be installed again by hand; `deploy.sh` is always taken from the commit being deployed.
2. Make a key pair on your computer: `ssh-keygen -t ed25519 -N '' -C github-deploy -f deploy_key`. Add `deploy_key.pub` to `/home/ubuntu/.ssh/authorized_keys` as one line:
   `restrict,command="/home/ubuntu/bin/crm-deploy-gate" ssh-ed25519 AAAA... github-deploy`
3. GitHub → Settings → Environments → new environment `production-server`: required reviewer (you), deployment branches limited to `main`. Add three environment secrets:
   - `DEPLOY_HOST`: the server's IP address
   - `DEPLOY_SSH_KEY`: the contents of `deploy_key` (the private key)
   - `DEPLOY_KNOWN_HOSTS`: the output of `ssh-keyscan -t ed25519 <ip>`, after checking its fingerprint matches `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server
4. Delete `deploy_key` from your computer and do a check-only run.
