# Strava workout sync

For runs you've tagged as a Workout on Strava, reads what you ran from the
laps, matches it to the session your coach prescribed by email, and writes your splits
into the activity description. Easy runs are skipped. Claude Code runs it headless
on your subscription, so no API key is needed.

| File | What it does |
|---|---|
| `workout-sync.md` | Instructions Claude Code follows on each run |
| `athlete.example.md` | Template for `athlete.md` (gitignored): your name, coach, emails, time zone |
| `normalize_laps.py` | Reads session structure from laps, repairs split reps, computes km splits and WU/CD times |
| `fetch_streams.py` | Downloads time/distance streams for km splits of unlapped efforts |
| `test_normalize_laps.py` | Synthetic lap cases (`python3 test_normalize_laps.py`) |
| `run_sync.sh` | Headless runner for cron or by hand |

## Setup

0. **Your details.** Copy `athlete.example.md` to `athlete.md` (gitignored) and
   fill in your name as it appears in the workout email, your coach, the email
   addresses to search and your time zone.
1. **Claude Code CLI.** The VS Code extension isn't enough for scheduled runs.
   In PowerShell: `irm https://claude.ai/install.ps1 | iex`, then run `claude`
   once to log in. Check that `claude --version` works in Git Bash.
2. **Strava API app.** At strava.com/settings/api, create an app (website and
   callback domain can be `localhost`). Note the Client ID and Secret.
3. **Write-capable Strava MCP.** Uses `manojanasuri16/STRAVA-MCP-SERVER`,
   cloned to `C:\dev\STRAVA-MCP-SERVER`. Authorize with only
   `activity:read_all,activity:write`, put the client ID, secret and refresh
   token in that folder's `.env`, run `uv sync`, then:
   `claude mcp add strava --scope user -- uv run --directory C:\dev\STRAVA-MCP-SERVER strava_server.py`.
   It can't set `workout_type`; tag workouts yourself on Strava.
4. **Gmail in Claude Code.** `run_sync.sh` uses the claude.ai Gmail connector
   (`mcp__claude_ai_Gmail__*`). Run `claude`, then `/mcp`, and confirm it's
   listed. If you use a different Gmail MCP server instead, change those names.
5. **Fix tool names.** Copy the exact Strava tool names from `/mcp` into
   `run_sync.sh`. Keep Gmail limited to search/read.
6. **Dry run first.** Run `DRY_RUN=1 ./run_sync.sh` and read `sync.log`. The
   write tool is withheld, and Claude prints what it would have written. Then
   run `./run_sync.sh` and check the first updated workout on Strava.
7. **Schedule it.** The Task Scheduler task "Strava Session Breakdown" runs
   `run_sync.sh` through Git Bash on Monday and Wednesday at 10pm and Saturday at
   3pm. With the 3-day lookback that covers every day of the week. A run missed
   while the PC was off starts when it's next on. Change the times in Task
   Scheduler, or remove the task with
   `Unregister-ScheduledTask "Strava Session Breakdown"`.

## How a workout is read

Laps come first, the email second:

1. `normalize_laps.py structure` describes the session from laps alone, e.g.
   `1×1724m @3:29/km · [2:09] · 10×~412m @2:53/km rec ~60s`. Laps faster than
   4:15/km count as fast (`FAST_PACE_S_PER_KM`); recoveries much longer than the
   rest are set breaks.
2. Claude matches that structure against the main sets in the email (rep count,
   rep length, recoveries, set breaks), so an email with both an XC and an MD set
   resolves to the one you actually ran. A tie goes to `review.md`.
3. Reps are measured with `normalize_laps.py reps` (below). Continuous efforts
   (tempo, 20' prog) get km splits from `normalize_laps.py splits`: from the laps
   if you lapped every km, otherwise from the activity's distance stream. The
   stream is lined up with laps by distance, since paused time is missing from
   laps but not from streams.

## How split laps are handled

`normalize_laps.py reps` assumes you press lap at the start and end of each rep:

- **Double-press slivers** (<30 m or <8 s) fold into the neighbouring lap.
- **Split reps:** consecutive fast laps are merged until they reach about one rep
  distance (85–120% of 400 m), so 230 m + 170 m becomes one rep.
- **Strides and short fast bits** that never reach rep length are ignored.
- **Missed lap presses** (two reps in one lap) can't be fixed from laps alone.
  The rep count then falls outside the plan's range, confidence goes `low`, and
  the activity goes to `review.md` instead of being written.

Repaired reps are marked `*` in the description, so you can spot-check them.

**Km autolap left on** is fine. Splits at a km since the last lap press, or at a
whole km since the start of the activity, are treated as autolap rather than
mistakes, so long reps aren't starred. Progressions lapped every km use those laps
for splits; if autolap was offset from the effort's start, the distance stream is
used instead. Easy km laps in the warmup and cooldown merge into one easy block.

**Every fast lap in the main set must end up in a rep.** If one doesn't (e.g. GPS
read a rep short), the workout goes to review, even when the rep count looks
plausible.

## Sessions split over several activities

Runs on the same day count as one session when each starts within 30 minutes of
the previous one ending (separate WU/CD runs, or saving after set 1).
`normalize_laps.py combine` joins their laps, with the time between activities as
a break, so the session is matched and checked as a whole (e.g. 2×5 with the
restart as the set break, 10 reps against "8–10"). Each tagged activity's
description then covers only its own part: its reps (keeping the session's rep
numbers), and its share of the warmup and cooldown. Untagged activities are read
but never written to.

## Indoors

A run with no GPS track (or an indoor track named in the email) runs in indoor
mode (`--indoor`, `"indoor": true`), because lap distances are then the watch's
accelerometer guess:

- Rep times are raw lap times, as outdoors.
- Fast laps are picked relative to the session's own top speed, and within the
  main set by splitting its laps into a fast and a slow cluster, so a watch that
  reads consistently short or long doesn't matter.
- Split reps are rebuilt by time (fragments adding up to 75–130% of the target)
  instead of by distance. Patterns read in times, e.g. `10×~1:12 rec ~60s`.
- Continuous efforts get their total time only; no km splits.

Tested on the two real outdoor sessions with distances distorted the way an
indoor watch might (uniformly 15–25% off, or ±10–20% random per lap): 86 of 88
exactly right, 2 sent to review, none silently wrong. These thresholds haven't
seen a real indoor session yet; check the first few against your watch.

Next step if it's ever needed: rebuild reps from the pace stream when laps are
unusable. That's a changepoint-detection problem.
