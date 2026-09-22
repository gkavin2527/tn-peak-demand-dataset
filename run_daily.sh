#!/usr/bin/env bash
# Script to run daily updates locally from your Mac (in India) and push to GitHub.
# Grid-India allows connections from Indian IPs, so this script can fetch peak demand.

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "=== Pulling latest changes from Git ==="
git pull --rebase

echo "=== Fetching weather forecast ==="
python3 fetch_weather.py forecast

echo "=== Fetching daily energy demand ==="
python3 fetch_demand.py

echo "=== Fetching peak demand (Grid-India) ==="
python3 update_peak_demand.py

echo "=== Fetching recent actual weather ==="
python3 fetch_weather.py update

echo "=== Rebuilding dataset ==="
python3 build_dataset.py

echo "=== Running health check ==="
python3 health_check.py

echo "=== Committing and pushing updates ==="
git add data/
if git diff --staged --quiet; then
    echo "No new data changes to commit."
else
    git commit -m "data: $(date +%Y-%m-%d)"
    git push origin main
    echo "✅ Changes pushed to GitHub successfully!"
fi
