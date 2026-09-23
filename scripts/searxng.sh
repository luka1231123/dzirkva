#!/bin/sh
# Start local SearXNG on http://127.0.0.1:8888
cd "$(dirname "$0")/.."
export $(grep SEARXNG_SECRET .env)
export SEARXNG_SETTINGS_PATH="$PWD/config/searxng.yml"
cd vendor/searxng && exec .venv/bin/python -m searx.webapp >> ../../data/searxng.log 2>&1
