#!/bin/bash
set -euo pipefail
cd /home/clement/bench/ipcbench
STAMP=$(date +%Y%m%d-%H%M%S)
cp /home/clement/bench/lukesdevlab/status.py /home/clement/bench/lukesdevlab/status.py.bak-$STAMP
cp /home/clement/bench/lukesdevlab/status.html /home/clement/bench/lukesdevlab/status.html.bak-$STAMP
PID=$(python3 - <<'PY'
import glob, os
for p in glob.glob('/proc/[0-9]*/cmdline'):
    try:
        c = open(p, 'rb').read().decode('utf-8', 'replace').replace('\0', ' ')
    except OSError:
        continue
    if 'status.py' in c and '8046' in c and 'python3 -c' not in c:
        print(p.split('/')[2])
        break
PY
)
if [ -n "$PID" ]; then
  kill "$PID"
  sleep 1
fi
PORT="${PORT:-8046}"
setsid nohup python3 dashboard/status.py --port "$PORT" --bind 0.0.0.0 \
  > /home/clement/bench/ipcbench/dashboard.log 2>&1 < /dev/null &
sleep 2
echo "dashboard backed up ($STAMP) and started on :$PORT"
echo "for a side-by-side smoke instance use: PORT=8047 $0"
