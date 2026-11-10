#!/usr/bin/env bash
# Deploys one commit of main to the production server. deploy/gate.sh runs it when the GitHub "Deploy" workflow connects.
#   check  - copy, build and test-run the update in a separate folder; nothing live changes.
#   deploy - the same, then switch the live site, verify, and roll back by itself if a check fails.
# It never changes the database or Python packages: if the update needs either, it stops and says what to run by hand.
set -u
MODE=$1
SHA=$2
D=$3
R=/home/ubuntu/CRM-
F=$R/frontend
VENV=$R/backend/venv
PUB=https://crm.perioxia.com
EXPECTED_PAGES="200/200/200/404/401"
# Internet scanners send fake Server Action requests; the website logs these rejections, they are not app errors.
PROBE='Server Reference ID did not match the expected format|Failed to find Server Action'
say() { echo "[$(date -u +%H:%M:%S)] $*"; }
stop() { say "STOPPED, nothing live changed: $*"; rm -rf "$D/src"; exit 1; }
page() { curl -s -o /dev/null -w '%{http_code}' -m 20 "$1"; }
pages() { echo "$(page $1/)/$(page $1/login)/$(page $1/privacy)/$(page $1/no-such-page)/$(page $1/api/auth/me)"; }
serves_build() { [ "$(curl -s -m 20 "$1/login" | grep -c "$2")" -ge 1 ]; }
port_busy() { [ -n "$(ss -ltnH "sport = :$1")" ]; }
port_pid() { sudo ss -ltnpH "sport = :$1" | grep -oP 'pid=\K[0-9]+' | head -1; }
rewrites() { python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["rewrites"], sort_keys=True))' "$1/.next/routes-manifest.json"; }
run_log() { sudo journalctl _SYSTEMD_INVOCATION_ID="$(systemctl show -p InvocationID --value $1)" --no-pager -q -o cat; }
wait_for() { for i in $(seq 1 60); do [ "$(page $1)" = "200" ] && return 0; sleep 1; done; return 1; }
case "$MODE" in check|deploy) ;; *) echo "unknown mode: $MODE"; exit 64 ;; esac
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "bad commit id"; exit 64; }
case "$D" in /home/ubuntu/deploy/cd-*) ;; *) echo "bad run folder: $D"; exit 64 ;; esac
cd "$D" || exit 1

# ---- 1. checks, no changes -------------------------------------------------------------------
OLD=$(git -C $R rev-parse HEAD)
[ "$(git -C $R branch --show-current)" = "main" ] || stop "the live checkout is not on main"
[ -z "$(git -C $R status --porcelain)" ] || stop "the live checkout has local changes"
[ "$OLD" = "$SHA" ] && { say "already live: ${SHA:0:7}"; exit 0; }
git -C $R merge-base --is-ancestor "$OLD" "$SHA" || stop "${SHA:0:7} does not build on the live commit ${OLD:0:7}"
systemctl is-active --quiet crm-frontend && systemctl is-active --quiet crm-backend || stop "a service is not running"
BEFORE=$(pages http://127.0.0.1:3000); [ "$BEFORE" = "$EXPECTED_PAGES" ] || stop "the website is not healthy before the deploy: $BEFORE"
[ "$(page http://127.0.0.1:8000/health)" = "200" ] || stop "the backend is not healthy before the deploy"
port_busy 3001 && stop "port 3001 is busy"
CHANGED=$(git -C $R diff --name-only "$OLD" "$SHA")
WEB=no; echo "$CHANGED" | grep -qE '^(frontend/|package(-lock)?\.json$)' && WEB=yes
API=no; echo "$CHANGED" | grep '^backend/' | grep -qvE '^backend/tests/|\.md$' && API=yes
BACKEND_PID=$(systemctl show crm-backend -p MainPID --value)
FRONTEND_PID=$(systemctl show crm-frontend -p MainPID --value)
OLD_BUILD_ID=$(cat $F/.next/BUILD_ID)
say "live ${OLD:0:7} -> ${SHA:0:7}, $(echo "$CHANGED" | grep -c .) files changed: website rebuild $WEB, backend restart $API"

# ---- 2. copy, check and build the new code in $D/src (live site untouched) -----------------------
mkdir -p src && git -C $R archive "$SHA" | tar -x -C src || stop "could not copy the code"
HEADS=$(cd src/backend && $VENV/bin/alembic heads 2>/dev/null | grep '(head)' | awk '{print $1}')
[ "$(echo "$HEADS" | grep -c .)" = "1" ] || stop "the new code should have exactly one migration head, found: $HEADS"
DB_REV=$(cd $R/backend && timeout 60 $VENV/bin/python -c '
import sqlalchemy as sa
from app.config import settings
with sa.create_engine(settings.get_database_url()).connect() as c:
    print(c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar())' 2>$D/db-version.log)
[ -n "$DB_REV" ] || stop "could not read the database version"
[ "$DB_REV" = "$HEADS" ] || stop "the database is at $DB_REV but the new code needs $HEADS: run the migration by hand first (DEPLOYMENT.md)"
if echo "$CHANGED" | grep -qx 'backend/requirements.txt'; then
  $VENV/bin/pip install --dry-run --quiet --report $D/pip-report.json -r src/backend/requirements.txt > $D/pip.log 2>&1 || stop "could not check the Python packages (see $D/pip.log)"
  NEED=$(python3 -c 'import json,sys; print(" ".join(i["metadata"]["name"] + "==" + i["metadata"]["version"] for i in json.load(open(sys.argv[1]))["install"]))' $D/pip-report.json)
  [ -z "$NEED" ] || stop "install these Python packages by hand first (DEPLOYMENT.md): $NEED"
fi
if [ $API = yes ]; then
  ( cd src/backend && timeout 120 env -i PATH=/usr/bin:/bin HOME=$D $VENV/bin/python -c 'import app.main' ) > $D/import.log 2>&1 || stop "the new backend code does not import (see $D/import.log)"
fi
say "database at $DB_REV as the new code expects; Python packages OK$([ $API = yes ] && echo '; new backend code imports cleanly')"
if [ $WEB = yes ]; then
  # oom_score_adj 1000: if memory runs out, the kernel kills this step, not the live backend or website.
  ( cd src/frontend && echo 1000 > /proc/self/oom_score_adj && nice -n 10 npm ci --workspaces=false --no-audit --no-fund ) > $D/install.log 2>&1 || stop "npm ci failed (see $D/install.log)"
  ( cd src/frontend && echo 1000 > /proc/self/oom_score_adj && NEXT_TELEMETRY_DISABLED=1 NODE_OPTIONS=--max-old-space-size=1536 nice -n 10 npm run --workspaces=false build ) > $D/build.log 2>&1 || stop "the website build failed (see $D/build.log)"
  grep -q "Downloading swc" $D/build.log && stop "the build tried to download the compiler"
  NEW_BUILD_ID=$(cat src/frontend/.next/BUILD_ID)
  [ "$(rewrites src/frontend)" = "$(rewrites $F)" ] || stop "the new build's /api routing differs from the live one"
  # Same command as the crm-frontend service, in its own process group so the stop below only hits it.
  ( cd src/frontend && echo 1000 > /proc/self/oom_score_adj && exec env NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production setsid npm run start -- --hostname 127.0.0.1 --port 3001 ) > $D/trial.log 2>&1 &
  TRIAL_PGID=$!
  wait_for http://127.0.0.1:3001/login
  TRIAL=$(pages http://127.0.0.1:3001)
  serves_build http://127.0.0.1:3001 "$NEW_BUILD_ID" && TRIAL_BUILD=ok || TRIAL_BUILD=wrong
  TRIAL_ERRS=$(grep -ciE 'error|exception|unhandled' $D/trial.log)
  [ "$(ps -o pgid= -p $TRIAL_PGID | tr -d ' ')" = "$TRIAL_PGID" ] && kill -TERM -- -$TRIAL_PGID 2>/dev/null
  for i in $(seq 1 20); do port_busy 3001 || break; sleep 1; done
  port_busy 3001 && stop "the test-run website on port 3001 did not stop (process group $TRIAL_PGID)"
  [ "$TRIAL" = "$EXPECTED_PAGES" ] || stop "the test run on port 3001 returned $TRIAL"
  [ "$TRIAL_BUILD" = "ok" ] || stop "the test run did not serve the new build"
  [ "$TRIAL_ERRS" = "0" ] || stop "$TRIAL_ERRS error lines in the test run ($D/trial.log)"
  say "website built ($(grep -oE 'Generating static pages \([0-9]+/[0-9]+\)$' $D/build.log | tail -1)) and test-run on port 3001: $TRIAL, 0 errors"
fi
if [ "$MODE" = "check" ]; then
  rm -rf src
  say "CHECK PASSED: ${SHA:0:7} is ready to deploy (website rebuild $WEB, backend restart $API); nothing live changed"
  exit 0
fi

# ---- 3. switch (website down ~5 s, plus ~8 s if the backend restarts) ----------------------------
cat > $D/ROLLBACK.txt <<NOTE
Deployed ${SHA:0:7} over ${OLD:0:7} at $(date -u +%Y-%m-%dT%H:%M:%SZ). Manual rollback, only if nothing was deployed after this:
  sudo systemctl stop crm-frontend
  git -C $R reset --hard $OLD
$([ $WEB = yes ] && echo "  rm -rf $F/.next $F/node_modules && mv $D/next.prev $F/.next && mv $D/node_modules.prev $F/node_modules")
$([ $API = yes ] && echo "  sudo systemctl restart crm-backend")
  sudo systemctl start crm-frontend
NOTE
rollback() {
  say "CHECK FAILED: $* -> rolling back to ${OLD:0:7}"
  [ $WEB = yes ] && sudo systemctl stop crm-frontend
  git -C $R reset -q --hard "$OLD"
  [ -d $D/next.prev ] && { rm -rf $F/.next; mv $D/next.prev $F/.next; }
  [ -d $D/node_modules.prev ] && { rm -rf $F/node_modules; mv $D/node_modules.prev $F/node_modules; }
  [ $API = yes ] && sudo systemctl restart crm-backend
  [ $WEB = yes ] && sudo systemctl start crm-frontend
  wait_for http://127.0.0.1:8000/health; wait_for http://127.0.0.1:3000/login
  NOW=$(git -C $R rev-parse --short HEAD); NOW_PAGES=$(pages http://127.0.0.1:3000); NOW_BUILD=$(cat $F/.next/BUILD_ID)
  if [ "$NOW" = "${OLD:0:7}" ] && [ "$NOW_PAGES" = "$EXPECTED_PAGES" ] && [ "$NOW_BUILD" = "$OLD_BUILD_ID" ] && [ "$(page http://127.0.0.1:8000/health)" = "200" ]; then
    say "ROLLED BACK OK: live is ${OLD:0:7} again, pages $NOW_PAGES"
  else
    say "ROLLBACK NEEDS ATTENTION: commit $NOW, pages $NOW_PAGES, build $NOW_BUILD (steps: $D/ROLLBACK.txt)"
  fi
  rm -rf "$D/src"
  exit 2
}
[ $WEB = yes ] && sudo systemctl stop crm-frontend
git -C $R merge -q --ff-only "$SHA" || rollback "git could not move the live checkout to ${SHA:0:7}"
if [ $WEB = yes ]; then
  mv $F/.next $D/next.prev && mv $F/node_modules $D/node_modules.prev || rollback "could not move the old website build aside"
  mv src/frontend/.next $F/.next && mv src/frontend/node_modules $F/node_modules || rollback "could not move the new website build in"
fi
[ $API = yes ] && { sudo systemctl restart crm-backend || rollback "the backend did not restart"; }
[ $WEB = yes ] && { sudo systemctl start crm-frontend || rollback "the website did not start"; }
wait_for http://127.0.0.1:8000/health || rollback "the backend did not answer /health within 60 s"
wait_for http://127.0.0.1:3000/login || rollback "the website did not answer within 60 s"
say "switched to ${SHA:0:7}"

# ---- 4. checks after the switch (any failure -> automatic rollback) ------------------------------
[ "$(git -C $R rev-parse HEAD)" = "$SHA" ] || rollback "the live checkout is not at ${SHA:0:7}"
LIVE=$(pages http://127.0.0.1:3000); [ "$LIVE" = "$EXPECTED_PAGES" ] || rollback "the website returned $LIVE"
PUBLIC=$(pages $PUB); [ "$PUBLIC" = "$EXPECTED_PAGES" ] || rollback "the public site returned $PUBLIC"
if [ $WEB = yes ]; then
  [ "$(cat $F/.next/BUILD_ID)" = "$NEW_BUILD_ID" ] && serves_build http://127.0.0.1:3000 "$NEW_BUILD_ID" && serves_build $PUB "$NEW_BUILD_ID" || rollback "the website is not serving the new build"
  NEXT_PID=$(port_pid 3000)
  [ -n "$NEXT_PID" ] && [ "$(readlink -f /proc/$NEXT_PID/exe)" = "/usr/bin/node" ] || rollback "the website is not running on /usr/bin/node"
else
  [ "$(systemctl show crm-frontend -p MainPID --value)" = "$FRONTEND_PID" ] || rollback "the website restarted although it should not have"
fi
if [ $API = yes ]; then
  [ "$(systemctl show crm-backend -p MainPID --value)" != "$BACKEND_PID" ] || rollback "the backend did not restart"
  for i in $(seq 1 30); do
    BOOTED=$(run_log crm-backend | grep -c 'Booting worker'); READY=$(run_log crm-backend | grep -c 'Application startup complete')
    [ "$BOOTED" -ge 1 ] && [ "$READY" = "$BOOTED" ] && break; sleep 1
  done
  [ "$READY" = "$BOOTED" ] || rollback "only $READY of $BOOTED backend workers started"
else
  [ "$(systemctl show crm-backend -p MainPID --value)" = "$BACKEND_PID" ] || rollback "the backend restarted although it should not have"
fi
sleep 10
if [ $API = yes ]; then
  API_ERRS=$(run_log crm-backend | grep -cE 'Traceback|\[ERROR\]|\[CRITICAL\]|" 5[0-9]{2}( |$)')
  [ "$API_ERRS" = "0" ] || rollback "$API_ERRS error lines in the backend log"
fi
if [ $WEB = yes ]; then
  WEB_ERRS=$(run_log crm-frontend | grep -iE 'error|exception|unhandled' | grep -cvE "$PROBE")
  [ "$WEB_ERRS" = "0" ] || rollback "$WEB_ERRS error lines in the website log"
fi
rm -rf src
# Keep the previous website build of the last two deploys for manual rollback; older copies are removed.
ls -dt /home/ubuntu/deploy/cd-*-deploy 2>/dev/null | tail -n +3 | while read -r old; do rm -rf "$old/next.prev" "$old/node_modules.prev"; done
say "DEPLOYED ${SHA:0:7}: pages $LIVE (public $PUBLIC), backend $([ $API = yes ] && echo restarted || echo untouched), website $([ $WEB = yes ] && echo "rebuilt ($NEW_BUILD_ID)" || echo untouched), 0 log errors"
say "memory: $(free -m | awk '/Mem:/ {print $7}') MB available, swap used $(free -m | awk '/Swap:/ {print $3}') MB; manual rollback steps: $D/ROLLBACK.txt"
