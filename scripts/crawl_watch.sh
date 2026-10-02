#!/bin/bash
# Run the crawler; restart it when data/crawl.log has been silent for 5 minutes (the crawler hangs after hours).
# Start:  nohup scripts/crawl_watch.sh > /dev/null 2>&1 &     Stop:  pkill -f crawl_watch.sh; pkill -f crawl_sites.py
cd "$(dirname "$0")/.." || exit 1
while true; do
  pkill -f crawl_sites.py
  sleep 3
  uv run python scripts/crawl_sites.py >> data/crawl.log 2>&1 &
  until [ $(( $(date +%s) - $(stat -f %m data/crawl.log) )) -gt 300 ]; do sleep 30; done
  echo "watchdog: log silent for 5 min, restart" >> data/crawl.log
done
