#!/usr/bin/env bash
# The only command the GitHub deploy key may run on the server. Installed as /home/ubuntu/bin/crm-deploy-gate
# and named in ~/.ssh/authorized_keys: restrict,command="/home/ubuntu/bin/crm-deploy-gate" ssh-ed25519 ...
# Accepts "check <commit>" or "deploy <commit>" for a commit on main, then runs that commit's deploy/deploy.sh.
# The deploy keeps running if the SSH connection drops; its log stays in the run folder.
set -u
REPO=/home/ubuntu/CRM-
read -r MODE SHA EXTRA <<< "${SSH_ORIGINAL_COMMAND:-}"
case "$MODE" in check|deploy) ;; *) echo "refused: expected 'check <commit>' or 'deploy <commit>'"; exit 64 ;; esac
[[ "$SHA" =~ ^[0-9a-f]{40}$ && -z "$EXTRA" ]] || { echo "refused: expected one full 40-character commit id"; exit 64; }
git -C $REPO fetch -q origin main || { echo "git fetch failed"; exit 1; }
git -C $REPO merge-base --is-ancestor "$SHA" origin/main || { echo "refused: $SHA is not on main"; exit 64; }
RUN=/home/ubuntu/deploy/cd-$(date -u +%Y%m%dT%H%M%SZ)-$MODE
mkdir -p $RUN && git -C $REPO show "$SHA:deploy/deploy.sh" > $RUN/deploy.sh || { echo "no deploy/deploy.sh in $SHA"; exit 1; }
bash -n $RUN/deploy.sh || { echo "refused: deploy/deploy.sh in $SHA does not parse"; exit 1; }
: > $RUN/run.log
setsid nohup flock -n -E 75 /home/ubuntu/deploy/.deploy.lock bash $RUN/deploy.sh "$MODE" "$SHA" "$RUN" >> $RUN/run.log 2>&1 < /dev/null &
PID=$!
tail -n +1 -f --pid=$PID $RUN/run.log
wait $PID
RC=$?
[ $RC = 75 ] && echo "refused: another deploy is running"
echo "exit $RC; server log: $RUN/run.log"
exit $RC
