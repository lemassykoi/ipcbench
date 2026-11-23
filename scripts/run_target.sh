#!/bin/bash
set -u
cd /home/clement/bench/ipcbench
T="${1:?usage: run_target.sh TARGET [--only ...]}"
shift
mkdir -p "results/$T"
python3 runner.py --target "$T" "$@" > "results/$T/runner.log" 2>&1
python3 reempty.py --target "$T" > "results/$T/reempty.log" 2>&1
python3 grade.py --target "$T" > "results/$T/grade.log" 2>&1
python3 report.py > "results/$T/report.log" 2>&1
echo "RUN_COMPLETE $T $(date +%F_%T)" > "results/$T/.complete"
