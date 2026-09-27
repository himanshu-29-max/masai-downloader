#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

# YouTube changes its anti-bot/SABR behavior often enough that a cached,
# already-satisfies-the->=-pin yt-dlp from a previous build can silently
# stay stale across deploys. Force it to the newest release every build.
pip install --upgrade --no-cache-dir yt-dlp