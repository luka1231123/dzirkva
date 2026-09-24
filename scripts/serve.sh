#!/bin/sh
# The public page as a launchd agent (config/com.dzirkva.web.plist): SearXNG + the web page.
# launchd starts it at login and again after every exit; a push to origin/main restarts it
# (.git/hooks/reference-transaction). caffeinate -s keeps the Mac awake on AC power only; on battery it sleeps.
# When the page does not answer within 5 min, one ntfy push goes to NTFY_TOPIC (.env); the next one only after
# a start that worked.
cd "$(dirname "$0")/.." || exit 1
export PATH="/opt/homebrew/bin:$PATH" LANG=en_US.UTF-8
PORT=$(sed -n 's/^PORT=//p' .env); PORT=${PORT:-8000}
TOPIC=$(sed -n 's/^NTFY_TOPIC=//p' .env)

pkill -f "python -m (searx.webapp|dzirkva.web)"; sleep 2  # copies started by hand
./scripts/searxng.sh &
caffeinate -s uv run python -m dzirkva.web &
web=$!
i=0
until curl -sf -o /dev/null "http://127.0.0.1:$PORT/about"; do
    i=$((i + 1))
    if [ $i -ge 300 ] || ! kill -0 $web 2>/dev/null; then
        echo "$(date '+%F %T') did not start"
        if [ -n "$TOPIC" ] && [ ! -e data/web.down ]; then
            touch data/web.down
            tail -3 data/web.log | curl -s -H "Title: dzirkva did not start" -H "Priority: high" \
                --data-binary @- "https://ntfy.sh/$TOPIC" >/dev/null
        fi
        exit 1
    fi
    sleep 1
done
rm -f data/web.down
echo "$(date '+%F %T') up on port $PORT"
wait $web
