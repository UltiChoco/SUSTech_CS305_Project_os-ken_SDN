#!/bin/bash
set -eu
PROJECT=/home/mininet/CS305-2026Spring-Project
CB=/home/mininet/software/miniconda3/envs/cs305/bin
LOG=/home/mininet/test_complex_run.log
PATH_ENV="$CB:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

sudo mn -c >/dev/null 2>&1 || true
for pid in $(pgrep -f "$CB/osken-manager --observe-links" || true); do kill -9 "$pid" 2>/dev/null || true; done
for pid in $(pgrep -f "$CB/python test_complex_shortest_path.py" || true); do kill -9 "$pid" 2>/dev/null || true; done
sleep 2

cd "$PROJECT"
nohup "$CB/osken-manager" --observe-links controller.py > /home/mininet/osken.log 2>&1 &
sleep 4

cd "$PROJECT/tests/switching_test"
rm -f "$LOG"
sudo env "PATH=$PATH_ENV" timeout 600 "$CB/python" test_complex_shortest_path.py 2>&1 | tee "$LOG"
echo "TEST_EXIT=${PIPESTATUS[0]}" >> "$LOG"
