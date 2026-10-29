#!/usr/bin/env bash
# Runs the workout sync headless. Schedule it (cron / Task Scheduler) or run by hand.
# Replace the Strava tool names below with the exact ones `/mcp` shows in Claude Code.
#   ./run_sync.sh             normal run
#   DRY_RUN=1 ./run_sync.sh   read everything, write nothing to Strava
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p tmp
export PATH="$HOME/.local/bin:$PATH"   # where the claude CLI installs; Task Scheduler's PATH lacks it

if ! command -v claude >/dev/null 2>&1; then
  echo "$(date): claude CLI not found on PATH" >> sync.log
  exit 1
fi

ALLOWED_TOOLS=(
  "mcp__strava__get_activities_by_date"    # list recent activities
  "mcp__strava__get_activity"
  "mcp__strava__get_activity_laps"
  "mcp__claude_ai_Gmail__search_threads"   # read-only Gmail (claude.ai connector)
  "mcp__claude_ai_Gmail__get_thread"
  "Bash(python3 normalize_laps.py:*)"
  "Bash(python3 fetch_streams.py:*)"       # time/distance streams for km splits
  "PowerShell(python3 normalize_laps.py:*)"   # in case Claude picks PowerShell on Windows
  "PowerShell(python3 fetch_streams.py:*)"
  "Read" "Write" "Edit"
)

if [[ ! -f athlete.md ]]; then
  echo "$(date): athlete.md missing; copy athlete.example.md and fill it in" >> sync.log
  exit 1
fi
PROMPT="$(cat workout-sync.md)"$'\n\n'"$(cat athlete.md)"
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  PROMPT+=$'\n\n**DRY RUN.** You have no Strava write tool. For each activity, print the
description block you would have written instead of updating it. Do not modify
processed.json or review.md.'
else
  ALLOWED_TOOLS+=("mcp__strava__update_activity")   # the only write tool
fi

{
  echo "=== $(date) ${DRY_RUN:+(dry run)} ==="
  claude -p "$PROMPT" --allowedTools "$(IFS=,; echo "${ALLOWED_TOOLS[*]}")"
} >> sync.log 2>&1
