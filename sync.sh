#!/bin/bash
# sync.sh — commit all local changes and push to GitHub.
#   bash sync.sh                 # commit with a timestamp message
#   bash sync.sh "what changed"  # commit with your own message
# Secrets (.env) and local state (data/, output/) are gitignored and never pushed.

set -uo pipefail
cd "/Users/davidglogoza/Claude/theater-recommender" || exit 1

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "No 'origin' remote yet. Create the GitHub repo first (see README), e.g.:"
  echo "  gh repo create theater-recommender --public --source=. --remote=origin --push"
  exit 1
fi

# Refresh the public GitHub Pages snapshot (docs/index.html) from the live DB.
/usr/bin/python3 render.py --publish >/dev/null 2>&1 || echo "warning: snapshot refresh failed"

if git diff --quiet && git diff --cached --quiet && [ -z "$(git status --porcelain)" ]; then
  echo "Nothing to sync — working tree is clean."
  exit 0
fi

MSG="${1:-sync: $(date '+%Y-%m-%d %H:%M')}"
git add -A
git commit -q -m "$MSG"
git push -q origin HEAD && echo "✓ synced to GitHub: $MSG"
