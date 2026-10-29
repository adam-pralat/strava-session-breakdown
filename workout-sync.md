# Workout sync

You are running unattended. Annotate my Strava workouts with what I actually ran,
matched to the session my coach prescribed. Who I am, my coach, the email
addresses and my time zone are in the **Athlete** section at the end.

Work in this folder. State lives in `processed.json` (activity IDs already handled)
and `review.md` (things a human should look at). Create them if missing.

**Running scripts:** use the Bash tool and run them exactly as shown below
(`python3 normalize_laps.py ...`, `python3 fetch_streams.py ...`), from this folder,
with no `cd`, pipes, `&&`, or redirects; anything else is blocked. If a script is
blocked or fails, **never work the numbers out yourself**: log the activity to
review.md with the error and don't touch Strava.

## Which activities

List my runs from the last 3 days (`get_activities_by_date`). Skip any already in
processed.json. For the rest, call `get_activity` and **only continue if
`workout_type` is 3 (Workout)**. Skip every other run silently and don't record it
in processed.json, so a run I tag as a Workout later still gets picked up.

**Sessions split over several activities.** Sometimes my warmup or cooldown is
its own activity, or I saved partway through the session. For each Workout, look
at my other runs that day: a run that starts within 30 minutes of the previous one
ending (end = start + elapsed time) is part of the same session, tagged or not, and
so is the next one chained to it. Handle the whole session once:
- Save each activity's laps to `tmp/laps_<id>.json` and write
  `tmp/session_<first id>.json` as `[{"id", "start" (start_date_local),
  "elapsed_s"}, ...]`. Run
  `python3 normalize_laps.py combine tmp/session_<first id>.json tmp/laps_session_<first id>.json`
  and use that combined file everywhere the steps below say `tmp/laps_<id>.json`.
  The time between activities shows up as a break.
- Match, measure and check the session as a whole (e.g. all 10 reps against
  "8–10"), but **each tagged activity's description covers only what it
  contains**. `segments` lists under `activities` which parts (warmup, main,
  cooldown) each activity holds. For an activity's own lines use
  `summary.by_activity[<id>]` from `reps` (keep the session's rep numbers, e.g.
  `6: 1:11 · … · 10: 1:09`) and `warmup.by_activity[<id>]` /
  `cooldown.by_activity[<id>]` from `segments` when those are split. Leave out
  lines for parts the activity doesn't hold.
- **Never write to an untagged activity**, even inside a session.
- Record every activity in the session in processed.json: tagged ones as
  `updated` / `review`, untagged ones as `session:<first id>`.

## For each workout (or session)

1. **Indoor or outdoor?** The run is **indoor** if `get_activity` has no
   `map_polyline` (no GPS track), or the email or activity name puts it on an
   indoor track. Indoors, lap distances are the watch's guess and can be off by
   15% or more, so add `--indoor` to every `structure`, `segments` and `splits`
   command below, and `"indoor": true` to the plan.

2. **Read the laps first.** They're the ground truth for what I ran. Save the raw
   lap list to `tmp/laps_<id>.json`, then run
   `python3 normalize_laps.py structure tmp/laps_<id>.json` (plus `--indoor`).
   Each `set` block has a `pattern` like `1×1724m @3:29/km · [2:09] · 10×~412m
   @2:53/km rec ~60s` (indoors in times instead: `10×~1:12 rec ~60s`): the bouts,
   the recoveries between them, and set breaks in brackets. Every bout has an id
   (`b1`, `b2`, ...).

3. **Find the workout email.** Search Gmail for
   `from:<coach's email> newer_than:5d` and also
   `subject:WO from:<my forwarding address> newer_than:5d` (manual forwards).
   Match by the day named in the subject ("Wed WO", "Mon WO") against the
   activity's local start date (in my time zone). Ignore day names in section
   headings inside the body; they are often copied from an earlier email.
   - No matching email: if the activity is under 2 days old, skip it without
     recording it (a forward may still arrive). Otherwise log it to review.md
     and record `"review"`.

4. **Match the laps to the email.** My target is on my line (see Athlete)
   ("3:15-10 = 78-76" means 3:15–3:10 per km = 78–76 s per 400 m). The email often
   has several main sets (e.g. XC and MD). Pick the one whose structure the laps
   show: rep count and length, recovery length, set breaks (e.g. "2×5 w1min, 3 min
   set rest" needs a break after rep 5), or one long continuous bout for a tempo
   or progression. A warmup progression before the main set is normal; don't count
   it. If two sets fit the laps equally well, or none does, log it to review.md
   with the structure pattern and skip.

5. **Measure the main set.** Save the session you matched to
   `tmp/plan_<id>.json` as `{"kind", "label", "effort", "warmup", "cooldown",
   "notes", ...}`, then:
   - **Reps** (`"kind": "reps"`, plus `"rep_distance_m", "reps_min",
     "reps_max", "target_s": [slow, fast], "recovery_s"`, and `"bouts":
     [first, last]`, the main set's first and last bout ids from step 2, leaving
     out any warmup progression): run
     `python3 normalize_laps.py reps tmp/laps_<id>.json tmp/plan_<id>.json`.
     It repairs accidentally split laps; trust its rep list over the raw laps.
   - **Continuous** (`"kind": "continuous"`, plus `"bout"` (the id from step 2),
     `"target"` as written, e.g. "20' prog 3:30→3:15"): run
     `python3 normalize_laps.py splits tmp/laps_<id>.json <bout>`. If it says the
     effort wasn't lapped per km, run `python3 fetch_streams.py <id>` and rerun
     with `tmp/streams_<id>.json` as the last argument. **Indoors there are no km
     splits:** write the effort as its total time only (e.g. `20' prog: 20:04`),
     no pace, and that `low` from `splits --indoor` doesn't block the write.
   - A session with both (e.g. tempo then reps) gets both, one section each.
   - **Warmup and cooldown:** run
     `python3 normalize_laps.py segments tmp/laps_<id>.json <first> <last>` with
     the main set's first and last bout ids. It gives warmup and cooldown
     **moving** time and their `pieces` in order: easy running, efforts (e.g. a
     warmup progression, with a bout id), and strides. Get km splits for each
     effort with `splits` as above.

6. **If every measurement's confidence is "high"**, add this block to the
   description. Keep any text already there above it. If the description already
   contains `— Session breakdown —` or `— workout sync —`, leave it alone.
   ```
   — Session breakdown —
   <main set>, <effort>
   Target <slow>→<fast> · <recovery> recovery
   1: 1:17.8 · 2: 1:17.1 · 3: 1:16.9 · ...
   Avg 1:16.8 (fastest 1:15.6, slowest 1:18.0)
   WU <warmup moving time>: <pieces, labelled from the email>
   CD <cooldown moving time>: <what the email says, e.g. easy>
   Auto-generated by Claude from my laps + coach's plan · https://github.com/adam-pralat/strava-sync
   ```
   For a continuous effort, instead of the rep lines:
   ```
   <label>: <distance> km @ <avg pace>/km · target <target>
   km: 3:35 · 3:30 · 3:27 · 3:22 · 3:18 · 0.8 km 2:39 (3:19/km)
   ```
   (a final partial km shows its distance, time, and pace.)
   The WU and CD times are the `moving` values from `segments`, never elapsed
   time. The WU line walks through the warmup `pieces` in order, labelled with
   the parts the email names, e.g. `WU 19:58: 12:01 easy · 6:01 prog @3:29/km
   (3:33, 0.7 km @3:23) · drills, 3 wickets, 3 strides`. Give pace and km splits
   only for efforts like a progression, never for easy running. In km splits, a
   final partial km is written `<distance> km @<pace>`, so it doesn't read as a
   full km. Times are m:ss.
   Rep times, the average/fastest/slowest, and the target come from the `display`
   values in the `reps` output: anything over 60 s is m:ss (`1:10.3`, `2:32.5`,
   target `2:52`). Write recoveries over 60 s the same way (`1:30 recovery`).
   Don't reformat these yourself.
   Mark repaired reps with `*` and add a line `* rebuilt from split laps` above
   the footer.
   Change nothing else: not the name, privacy, gear, workout type, or anything on
   other activities.

7. **If any confidence is "low"**, don't touch Strava. Append the activity link,
   the structure pattern, the matched session, and the warnings to review.md.

8. Add the activity ID and outcome (`updated` / `review`) to processed.json
   (for a split session, every activity in it, as above).

Never send, reply to, or delete email. End with a one-line summary per workout,
plus a count of non-workout runs skipped.
