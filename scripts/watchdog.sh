#!/bin/sh
# Restarts scripts/build_passages.py when data/passages.log has no new line for 10 min (stall).
# The build is resumable, so a restart loses at most one batch. Stops when the build prints "done".
# Run: nohup ./scripts/watchdog.sh > data/watchdog.log 2>&1 &
cd "$(dirname "$0")/.." || exit 1
LOG=data/passages.log
while ! grep -q "^done" "$LOG"; do
    sleep 60
    age=$(( $(date +%s) - $(stat -f %m "$LOG") ))
    if [ "$age" -gt 600 ] || ! pgrep -f "^[^ ]*python scripts/build_passages" >/dev/null; then
        echo "$(date '+%H:%M') no progress for ${age}s: restart"
        pkill -f "^[^ ]*python scripts/build_passages"; sleep 10
        nohup uv run python scripts/build_passages.py >> "$LOG" 2>&1 &
    fi
done
echo "$(date '+%H:%M') build done"
