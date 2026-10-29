#!/usr/bin/env python3
"""Read a workout from raw Strava laps: find its structure, repair split reps,
and compute km splits for continuous efforts.

Usage:
    python3 normalize_laps.py structure laps.json [--indoor]
    python3 normalize_laps.py reps      laps.json plan.json
    python3 normalize_laps.py splits    laps.json <bout id> [streams.json] [--split-m 1000] [--indoor]
    python3 normalize_laps.py segments  laps.json <first bout> <last bout> [--indoor]
    python3 normalize_laps.py combine   session.json out.json

laps.json     Strava lap objects (uses "distance" in m and "elapsed_time" in s;
              "distance_m"/"elapsed_time_s" are also accepted).
plan.json     {"rep_distance_m": 400, "reps_min": 8, "reps_max": 10,
               "target_s": [78, 76], "recovery_s": 60,
               "bouts": ["b2", "b11"],   (optional: main set's first/last bout)
               "indoor": true}           (optional: lap distances can't be trusted)
streams.json  {"time": [...], "distance": [...]} from fetch_streams.py. Only needed
              when the effort wasn't lapped per split.

structure  Plan-free view of the session: easy running, sets of fast bouts with
           their recoveries and set breaks, and strides. Each bout has an id
           ("b1", "b2", ...) that `splits` accepts.
reps       Interval reps against a plan, repairing accidental lap splits.
splits     Per-km (or --split-m) splits for one bout, e.g. a tempo or progression.
segments   Warmup / main set / cooldown times, given the main set's first and last
           bout. Faster efforts inside the warmup or cooldown (e.g. a progression)
           are listed with their bout ids.

combine    Join a session split over several activities into one laps file. Each
           lap gets its "activity_id", and the time between activities becomes a
           break. reps, segments and splits then report per activity too.

reps and splits print a "confidence" ("high"/"low"). Only write to Strava when it
is "high".

Assumes reps and recoveries are lapped manually (lap button per rep).

Indoors (--indoor, or "indoor": true in the plan) lap distances come from the
watch's accelerometer and can be off by 15% or more. Fast laps are then picked
relative to the session's own top speed instead of a fixed pace, reps are sized
by time against the target instead of by distance, and `splits` refuses.
"""
import json
import sys

MICRO_DIST_M = 30      # laps shorter than this are accidental double-presses
MICRO_TIME_S = 8
WORK_SPEED_FACTOR = 0.8   # work = faster than 80% of the slowest target speed
REP_MIN_FRAC = 0.85       # a rep must cover >= 85% of the rep distance
REP_MAX_FRAC = 1.2        # ...and <= 120%

FAST_PACE_S_PER_KM = 255  # structure: laps faster than 4:15/km count as fast
STRIDE_MAX_M = 150        # fast bouts shorter than this are strides
MAX_RECOVERY_S = 300      # a slower stretch longer than this ends a set
SET_BREAK_FACTOR = 1.8    # a recovery this many times the median is a set break
SET_BREAK_MIN_S = 90
SAME_REP_TOLERANCE = 0.25  # bouts within 25% of each other are the same rep length
SPLIT_LAP_TOLERANCE = 0.1  # laps within 10% of --split-m mean it was lapped per split
AUTOLAP_M = 1000           # watch autolap distance; splits at it aren't lap mistakes
AUTOLAP_TOLERANCE_M = 15
STREAM_MISMATCH = 0.05     # stream vs lap distance disagreement that makes splits low

RELATIVE_FAST = 0.75       # indoors: fast = at least 75% of the session's top speed
TOP_SPEED_SHARE = 0.1      # top speed = speed of the fastest 10% of running time
TOP_SPEED_MIN_LAP_S = 20   # ...ignoring laps shorter than this (strides)
STRIDE_MAX_S = 25          # indoors: fast bouts shorter than this are strides
INDOOR_REP_MIN_FRAC = 0.75  # indoors: a rep takes 75-130% of the target time
INDOOR_REP_MAX_FRAC = 1.3


def _lap(distance, time, start=0.0, moving=None):
    return {"distance": float(distance), "time": float(time), "start": start, "parts": 1,
            "frags": [(start, float(distance))],
            "moving": float(time if moving is None else moving)}


def _join(a, b):
    return {"distance": a["distance"] + b["distance"],
            "time": a["time"] + b["time"],
            "moving": a["moving"] + b["moving"],
            "start": a["start"],
            "parts": a["parts"] + b["parts"],
            "frags": a["frags"] + b["frags"],
            "activity": a.get("activity")}


def _near(x, target):
    return abs(x - target) <= AUTOLAP_TOLERANCE_M


def _autolap_only(rep):
    """True if every split inside this rep is where autolap would fire: a km after
    the previous lap press, or on a whole km since the start of the activity."""
    frags = rep["frags"]
    return len(frags) > 1 and all(
        _near(prev_d, AUTOLAP_M) or (start >= AUTOLAP_M / 2 and
                                     _near(start, round(start / AUTOLAP_M) * AUTOLAP_M))
        for (_, prev_d), (start, _) in zip(frags, frags[1:]))


def _is_micro(lap):
    return not lap.get("gap") and (lap["distance"] < MICRO_DIST_M or
                                   lap["time"] < MICRO_TIME_S)


def _field(lap, *keys):
    for k in keys:
        if lap.get(k) is not None:
            return lap[k]
    raise KeyError(f"lap has none of {keys}: {lap}")


def load_laps(raw):
    # Raw Strava API laps use distance/elapsed_time; some MCP servers add unit suffixes.
    # Laps are contiguous, so each starts where the previous one ended. Track start
    # by distance: paused time is missing from laps but not from streams.
    # In a combined session (see `combine`) distance restarts with each activity,
    # like its streams do, and the time between activities is a "gap" lap.
    laps, d, act = [], 0.0, None
    for l in raw:
        if l.get("gap"):
            laps.append({**_lap(0, l["elapsed_time"], 0.0, 0), "gap": True, "frags": [],
                         "activity": None})
            continue
        if l.get("activity_id") != act:
            act, d = l.get("activity_id"), 0.0
        lap = _lap(_field(l, "distance", "distance_m"),
                   _field(l, "elapsed_time", "elapsed_time_s", "moving_time", "moving_time_s"), d,
                   _field(l, "moving_time", "moving_time_s", "elapsed_time", "elapsed_time_s"))
        lap["activity"] = act
        d += lap["distance"]
        laps.append(lap)
    return laps


def _absorb(a, b):
    """_join, but the sliver extends a's last piece instead of adding a split point."""
    j = _join(a, b)
    (s0, _), (s1, _) = j["frags"][-2], j["frags"][-1]
    j["frags"] = j["frags"][:-2] + [(min(s0, s1), j["frags"][-2][1] + j["frags"][-1][1])]
    return j


def merge_micro(laps):
    """Fold double-press slivers into the neighbouring lap."""
    out = []
    for lap in laps:
        if _is_micro(lap) and out and not out[-1].get("gap"):
            out[-1] = _absorb(out[-1], lap)
        else:
            out.append(dict(lap))
    if len(out) > 1 and _is_micro(out[0]):
        out[1] = _absorb(out[0], out[1])
        out.pop(0)
    return out


def rep_clock(seconds, decimals=1):
    """Rep time for the description: 58.3 up to a minute, 1:10.3 over it."""
    s = round(seconds, decimals)
    if s <= 60:
        return f"{s:.{decimals}f}"
    m = int(s // 60)
    width = 3 + decimals if decimals else 2
    return f"{m}:{s - 60 * m:0{width}.{decimals}f}"


def pace(distance, time):
    """Time per km as m:ss."""
    s = round(time * 1000 / distance) if distance else 0
    return f"{s // 60}:{s % 60:02d}"


def clock(seconds):
    """Duration as m:ss, or h:mm:ss from an hour."""
    s = round(seconds)
    h, m = s // 3600, s % 3600 // 60
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[n // 2] + xs[(n - 1) // 2]) / 2 if xs else 0


# ---------------------------------------------------------------- structure

def _speed(lap):
    return lap["distance"] / lap["time"] if lap["time"] else 0.0


def _top_speed(laps):
    """Speed of the session's fastest running, robust to a single odd lap."""
    runs = sorted((l for l in laps if l["time"] >= TOP_SPEED_MIN_LAP_S and not l.get("gap")),
                  key=_speed, reverse=True)
    budget = TOP_SPEED_SHARE * sum(l["time"] for l in runs)
    for l in runs:
        budget -= l["time"]
        if budget <= 0:
            return _speed(l)
    return _speed(runs[-1]) if runs else 0.0


def _fast(laps, indoor=False):
    if not indoor:
        return [_speed(l) >= 1000 / FAST_PACE_S_PER_KM for l in laps]
    # A watch that reads short or long indoors does so on every lap, so compare
    # laps with each other, not with a fixed pace.
    top = _top_speed(laps)
    return [top > 0 and _speed(l) >= RELATIVE_FAST * top for l in laps]


def _speed_split(speeds):
    """Cutoff between the slow and fast cluster (best 2-way split of log speeds)."""
    import math
    xs = sorted(math.log(s) for s in speeds if s > 0)
    if len(xs) < 2:
        return 0.0
    best, cut = None, 0.0
    for k in range(1, len(xs)):
        lo_, hi_ = xs[:k], xs[k:]
        cost = sum((x - sum(lo_) / len(lo_)) ** 2 for x in lo_) + \
            sum((x - sum(hi_) / len(hi_)) ** 2 for x in hi_)
        if best is None or cost < best:
            best, cut = cost, math.exp((xs[k - 1] + xs[k]) / 2)
    return cut


def _is_stride(bout, indoor=False):
    return bout["time"] < STRIDE_MAX_S if indoor else bout["distance"] < STRIDE_MAX_M


def _bouts(laps, indoor=False):
    """Maximal runs of consecutive fast laps, numbered b1, b2, ..."""
    fast = _fast(laps, indoor)
    bouts, i = [], 0
    while i < len(laps):
        if not fast[i]:
            i += 1
            continue
        first = i
        while i < len(laps) and fast[i]:
            i += 1
        acc = laps[first]
        for l in laps[first + 1:i]:
            acc = _join(acc, l)
        bouts.append({"id": f"b{len(bouts) + 1}", "first": first, "last": i - 1,
                      "laps": [laps[k] for k in range(first, i)], **acc})
    return bouts


def _bout_view(b):
    v = {"id": b["id"], "distance_m": round(b["distance"]), "time_s": round(b["time"], 1),
         "pace": pace(b["distance"], b["time"]), "start_m": round(b["start"])}
    if len(b["laps"]) > 1:
        v["lap_distances_m"] = [round(l["distance"]) for l in b["laps"]]
    return v


def _pattern(bouts, recoveries, breaks, indoor=False):
    """Compact text like '1×1724m @3:29 · [2:09] · 10×~413m @2:50 rec ~60s'.
    Indoors, by time instead: '1×6:01 · [2:09] · 10×~1:12 rec ~60s'."""
    size = "time" if indoor else "distance"
    groups, cur = [], [0]
    for k in range(1, len(bouts)):
        ref = _median([bouts[j][size] for j in cur])
        if (k - 1) in breaks or abs(bouts[k][size] - ref) > SAME_REP_TOLERANCE * ref:
            groups.append(cur)
            cur = [k]
        else:
            cur.append(k)
    groups.append(cur)

    parts = []
    for g in groups:
        if parts:
            gap = recoveries[g[0] - 1]
            parts.append(f"[{int(gap // 60)}:{round(gap % 60):02d}]")
        approx = "~" if len(g) > 1 else ""
        if indoor:
            text = f"{len(g)}×{approx}{clock(_median([bouts[k]['time'] for k in g]))}"
        else:
            d = _median([bouts[k]["distance"] for k in g])
            dist = sum(bouts[k]["distance"] for k in g)
            t = sum(bouts[k]["time"] for k in g)
            text = f"{len(g)}×{approx}{d:.0f}m @{pace(dist, t)}/km"
        recs = [recoveries[k] for k in g[:-1]]
        if recs:
            text += f" rec ~{_median(recs):.0f}s"
        parts.append(text)
    return " · ".join(parts)


def structure(raw_laps, indoor=False):
    laps = merge_micro(load_laps(raw_laps))
    bouts = _bouts(laps, indoor)
    blocks = []

    def easy(lo, hi):
        if lo <= hi:
            d = sum(laps[k]["distance"] for k in range(lo, hi + 1))
            t = sum(laps[k]["time"] for k in range(lo, hi + 1))
            blocks.append({"kind": "easy", "distance_m": round(d), "time_s": round(t),
                           "pace": pace(d, t)})

    def gap(a, b):
        return sum(laps[k]["time"] for k in range(a["last"] + 1, b["first"]))

    # Strides are reported but never join a set.
    real = [b for b in bouts if not _is_stride(b, indoor)]
    strides = [b for b in bouts if _is_stride(b, indoor)]

    sets, cur = [], []
    for b in real:
        if cur and gap(cur[-1], b) > MAX_RECOVERY_S:
            sets.append(cur)
            cur = []
        cur.append(b)
    if cur:
        sets.append(cur)

    pos = 0
    for s in sets:
        before = [b for b in strides if pos <= b["first"] < s[0]["first"]]
        if before:
            easy(pos, before[0]["first"] - 1)
            blocks.append({"kind": "strides", "count": len(before)})
        else:
            easy(pos, s[0]["first"] - 1)
        recoveries = [gap(s[k], s[k + 1]) for k in range(len(s) - 1)]
        med = _median(recoveries)
        breaks = [k for k, r in enumerate(recoveries)
                  if r >= SET_BREAK_MIN_S and r >= SET_BREAK_FACTOR * med]
        blocks.append({
            "kind": "set",
            "pattern": _pattern(s, recoveries, breaks, indoor),
            "bouts": [_bout_view(b) for b in s],
            "recoveries_s": [round(r) for r in recoveries],
            "set_breaks_after": [s[k]["id"] for k in breaks],
        })
        pos = s[-1]["last"] + 1
    easy(pos, len(laps) - 1)
    trailing = [b for b in strides if b["first"] >= pos]
    if trailing:
        blocks.append({"kind": "strides", "count": len(trailing)})
    out = {"blocks": blocks}
    if indoor:
        out["indoor"] = True
    return out


# ---------------------------------------------------------------- reps

def normalize(raw_laps, plan):
    rep_d = plan["rep_distance_m"]
    slowest_target = max(plan["target_s"])
    work_speed = WORK_SPEED_FACTOR * rep_d / slowest_target
    indoor = bool(plan.get("indoor"))
    # Outdoors reps are sized by distance; indoors by time against the target.
    if indoor:
        size, unit = (lambda l: l["time"]), "s"
        rep_size = sum(plan["target_s"]) / len(plan["target_s"])
        lo, hi = INDOOR_REP_MIN_FRAC * rep_size, INDOOR_REP_MAX_FRAC * rep_size
    else:
        size, unit = (lambda l: l["distance"]), "m"
        rep_size = rep_d
        lo, hi = REP_MIN_FRAC * rep_size, REP_MAX_FRAC * rep_size
    warnings = []

    laps = merge_micro(load_laps(raw_laps))
    fast = _fast(laps, indoor) if indoor else None   # relative to the whole session
    if plan.get("bouts"):
        # Main set picked from `structure`: count reps only from its first bout to
        # its last, so a fast warmup progression can't pass for a long rep.
        by_id = {b["id"]: b for b in _bouts(laps, indoor)}
        first, last = plan["bouts"][0], plan["bouts"][-1]
        if first not in by_id or last not in by_id:
            return {"reps": [], "summary": {}, "confidence": "low",
                    "warnings": [f"Unknown bout {first} or {last}; rerun structure."]}
        a, b = by_id[first]["first"], by_id[last]["last"] + 1
        if indoor:
            # The main set is only reps and recoveries, far apart in speed, so split
            # its laps into two speed clusters: sturdier against per-lap distance
            # noise than one cutoff for the whole session.
            thr = _speed_split([_speed(l) for l in laps[a:b]])
            fast = [_speed(l) >= thr for l in laps[a:b]]
        laps = laps[a:b]
        fast = fast if indoor else None
    is_work = fast if indoor else [_speed(l) >= work_speed for l in laps]

    reps, long_laps, stray, i = [], [], [], 0
    while i < len(laps):
        if not is_work[i]:
            i += 1
            continue
        # A run of consecutive fast laps: rebuild reps from the fragments.
        group = []
        while i < len(laps) and is_work[i]:
            group.append(laps[i])
            i += 1
        acc = None
        reps_before = len(reps)
        for frag in group:
            acc = frag if acc is None else _join(acc, frag)
            if size(acc) >= lo:
                if size(acc) <= hi:
                    reps.append(acc)
                elif size(acc) >= 1.8 * rep_size:
                    long_laps.append((size(acc), len(reps)))
                else:
                    stray.append(size(acc))
                acc = None
        if acc is not None:
            if len(reps) > reps_before and size(reps[-1]) + size(acc) <= hi:
                reps[-1] = _join(reps[-1], acc)   # tail of a split rep
            else:
                stray.append(size(acc))   # a stride, or a rep that didn't measure up

    # A long fast lap between reps is probably a missed lap press. Before the first
    # rep or after the last it's a warmup progression or tempo finish; a missed press
    # there still shows up as a rep count outside the plan.
    for amount, reps_before_it in long_laps:
        if 0 < reps_before_it < len(reps):
            warnings.append(f"Fast lap of {amount:.0f} {unit} between reps looks like a "
                            f"missed lap press; not counted as reps.")
    # Inside a main set picked from `structure`, every fast lap should be (part of) a
    # rep. One that isn't means a miscount, even when the count looks plausible.
    if plan.get("bouts") and stray:
        warnings.append(f"Fast lap(s) of {', '.join(f'{x:.0f}' for x in stray)} {unit} "
                        f"inside the main set didn't fit a rep of {rep_size:.0f} {unit}.")

    out_reps = []
    for n, r in enumerate(reps, 1):
        out_reps.append({
            "n": n,
            "distance_m": round(r["distance"]),
            "time_s": round(r["time"], 1),
            "time_per_rep_distance_s": round(r["time"] * rep_d / r["distance"], 1),
            "merged_from_laps": r["parts"],
            "autolap_split": not indoor and _autolap_only(r),
            "activity_id": r.get("activity"),
        })

    count = len(out_reps)
    if not plan["reps_min"] <= count <= plan["reps_max"]:
        warnings.append(f"Found {count} reps; plan said {plan['reps_min']}-{plan['reps_max']}.")
    merged = [r["n"] for r in out_reps
              if r["merged_from_laps"] > 1 and not r["autolap_split"]]

    # Raw lap times: reps are lapped at marks (track, measured loop), so the watch's
    # time is right and its GPS distance is the noisy part.
    summary = _summary(out_reps, merged, plan)
    acts = list(dict.fromkeys(r["activity_id"] for r in out_reps))
    if len(acts) > 1:
        # Each activity's description covers only its own reps (numbered as in
        # the whole session).
        summary["by_activity"] = {
            a: _summary([r for r in out_reps if r["activity_id"] == a], merged, plan)
            for a in acts}

    low = bool(warnings) or count == 0
    return {"reps": out_reps, "summary": summary,
            "warnings": warnings, "confidence": "low" if low else "high"}


def _summary(out_reps, merged, plan):
    times = [r["time_s"] for r in out_reps]
    if not times:
        return {}
    count = len(times)
    summary = {"count": count,
               "reps": [r["n"] for r in out_reps],
               "avg_s": round(sum(times) / count, 1),
               "fastest_s": min(times),
               "slowest_s": max(times),
               "repaired_reps": [n for n in merged if n in {r["n"] for r in out_reps}]}
    dp = 0 if all(t == int(t) for t in times) else 1   # Strava laps are whole seconds
    summary["display"] = {
        "reps": [rep_clock(t, dp) for t in times],
        "avg": rep_clock(summary["avg_s"]),
        "fastest": rep_clock(summary["fastest_s"], dp),
        "slowest": rep_clock(summary["slowest_s"], dp),
        "target": [rep_clock(t, 0) for t in plan["target_s"]],
    }
    return summary


# ---------------------------------------------------------------- splits

def _interp(xs, ys, x):
    """y at x, with xs ascending."""
    if x <= xs[0]:
        return ys[0]
    for k in range(1, len(xs)):
        if xs[k] >= x:
            x0, x1, y0, y1 = xs[k - 1], xs[k], ys[k - 1], ys[k]
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return ys[-1]


def _start_time(ds, ts, d):
    """When the athlete leaves distance d: skips any standing still after the lap press."""
    k = 0
    while k + 1 < len(ds) and ds[k + 1] <= d + 1:
        k += 1
    return ts[k] if ds[k] >= d - 1 else _interp(ds, ts, d)


def _end_time(ds, ts, d):
    """When the athlete reaches distance d (within 1 m): stops before any standing
    still at the lap press, which a hair of rounding would otherwise skip past."""
    exact = _interp(ds, ts, d)
    if exact - _interp(ds, ts, d - 1) <= 2:
        return exact
    # Over 2 s for the last metre: standing. Use the sample where they arrived.
    return next(t for t, x in zip(ts, ds) if x >= d - 1)


def _split_rows(marks):
    """marks: [(distance_from_start, time_from_start), ...] at each split point."""
    rows = []
    for (d0, t0), (d1, t1) in zip(marks, marks[1:]):
        d, t = d1 - d0, t1 - t0
        rows.append({"n": len(rows) + 1, "distance_m": round(d), "time_s": round(t, 1),
                     "pace": pace(d, t)})
    return rows


def splits(raw_laps, bout_id, streams=None, split_m=1000, indoor=False):
    laps = merge_micro(load_laps(raw_laps))
    bout = next((b for b in _bouts(laps, indoor) if b["id"] == bout_id), None)
    if bout is None:
        return {"splits": [], "warnings": [f"No bout {bout_id}."], "confidence": "low"}
    if indoor:
        return {"bout": {"id": bout_id, "time": clock(bout["time"])}, "splits": [],
                "confidence": "low",
                "warnings": ["Indoors: distance isn't reliable enough for km splits. "
                             "Report the effort's total time only."]}
    warnings = []
    lap_ds = [l["distance"] for l in bout["laps"]]

    if len(lap_ds) > 1 and all(abs(d - split_m) <= SPLIT_LAP_TOLERANCE * split_m
                               for d in lap_ds[:-1]):
        source = "laps"
        marks, d, t = [(0.0, 0.0)], 0.0, 0.0
        for l in bout["laps"]:
            d, t = d + l["distance"], t + l["time"]
            marks.append((d, t))
    elif streams is not None:
        # In a combined session, pass the stream of the bout's own activity;
        # lap starts restart at 0 per activity just like its stream does.
        source = "streams"
        ts, ds = streams["time"], streams["distance"]
        d_start, d_end = bout["start"], bout["start"] + bout["distance"]
        t_start, t_end = _start_time(ds, ts, d_start), _end_time(ds, ts, d_end)
        if abs((t_end - t_start) - bout["time"]) > STREAM_MISMATCH * bout["time"]:
            warnings.append(f"Stream shows {t_end - t_start:.0f} s for this bout but laps "
                            f"say {bout['time']:.0f} s; streams and laps may be misaligned "
                            f"(or the watch was paused mid-effort).")
        marks, target = [(0.0, 0.0)], d_start + split_m
        while target < d_end:
            marks.append((target - d_start, _interp(ds, ts, target) - t_start))
            target += split_m
        marks.append((d_end - d_start, t_end - t_start))
    else:
        return {"splits": [], "confidence": "low",
                "warnings": ["Not lapped per split; pass a streams file from fetch_streams.py."]}

    rows = _split_rows(marks)
    if rows and rows[-1]["distance_m"] < 0.1 * split_m:   # GPS crumb past the last mark
        rows.pop()
    return {"bout": _bout_view(bout), "activity_id": bout.get("activity"),
            "source": source, "split_m": split_m,
            "splits": rows, "warnings": warnings,
            "confidence": "low" if warnings or not rows else "high"}


# ---------------------------------------------------------------- segments

def segments(raw_laps, first_id, last_id, indoor=False):
    laps = merge_micro(load_laps(raw_laps))
    bouts = _bouts(laps, indoor)
    by_id = {b["id"]: b for b in bouts}
    if first_id not in by_id or last_id not in by_id:
        return {"error": f"Unknown bout: {first_id} or {last_id}."}
    lo, hi = by_id[first_id]["first"], by_id[last_id]["last"]

    def pieces(a, b):
        """Easy running, efforts and strides in order. Easy and stride times are
        moving time; efforts keep their lap time, like `splits`."""
        bout_at = {k: x for x in bouts for k in range(x["first"], x["last"] + 1)}
        out, k = [], a
        while k < b:
            if laps[k].get("gap"):
                k += 1
                continue
            x = bout_at.get(k)
            if x and not _is_stride(x, indoor):
                out.append({"kind": "effort", "time": clock(x["time"]), **_bout_view(x)})
                k = x["last"] + 1
                continue
            kind = "strides" if x else "easy"
            if out and out[-1]["kind"] == kind:
                out[-1]["moving_s"] += laps[k]["moving"]
            else:
                out.append({"kind": kind, "moving_s": laps[k]["moving"]})
            if kind == "strides":
                out[-1]["count"] = out[-1].get("count", 0) + 1
            k += 1
        # Jogs of under 2 min between strides belong to the strides.
        merged = []
        for p in out:
            if merged and merged[-1]["kind"] == "strides" and p["kind"] in ("strides", "easy") \
                    and (p["kind"] == "strides" or p["moving_s"] < 120):
                merged[-1]["moving_s"] += p["moving_s"]
                merged[-1]["count"] += p.get("count", 0)
            else:
                merged.append(p)
        for p in merged:
            if "moving_s" in p:
                p["moving"] = clock(p["moving_s"])
                p["moving_s"] = round(p["moving_s"])
        return merged

    def part(a, b, split=True):
        span = [l for l in laps[a:b] if not l.get("gap")]
        t = sum(l["moving"] for l in span)
        out = {"moving": clock(t), "moving_s": round(t),
               "distance_m": round(sum(l["distance"] for l in span)),
               "pieces": pieces(a, b)}
        acts = list(dict.fromkeys(l["activity"] for l in span))
        if split and len(acts) > 1:
            # A warmup or cooldown spread over activities: each one's own share.
            out["by_activity"] = {}
            for act in acts:
                ks = [k for k in range(a, b) if laps[k].get("activity") == act]
                out["by_activity"][act] = part(ks[0], ks[-1] + 1, split=False)
        return out

    main = [l for l in laps[lo:hi + 1] if not l.get("gap")]
    out = {"warmup": part(0, lo),
           "main": {"elapsed": clock(sum(l["time"] for l in main)),
                    "distance_m": round(sum(l["distance"] for l in main))},
           "cooldown": part(hi + 1, len(laps))}
    acts = list(dict.fromkeys(l["activity"] for l in laps if not l.get("gap")))
    if len(acts) > 1:
        # Which part of the session each activity holds, for its own description.
        out["activities"] = {
            act: [name for name, (a, b) in (("warmup", (0, lo)), ("main", (lo, hi + 1)),
                                            ("cooldown", (hi + 1, len(laps))))
                  if any(l.get("activity") == act for l in laps[a:b])]
            for act in acts}
    return out


# ---------------------------------------------------------------- combine

def combine(session, laps_dir):
    """session: [{"id", "start": local ISO time, "elapsed_s"}, ...], one per
    activity; laps are read from <laps_dir>/laps_<id>.json."""
    from datetime import datetime, timedelta
    from pathlib import Path

    def when(a):
        return datetime.fromisoformat(a["start"].replace("Z", ""))

    out, prev_end = [], None
    for a in sorted(session, key=when):
        start = when(a)
        if prev_end is not None:
            out.append({"gap": True,
                        "elapsed_time": max(0.0, (start - prev_end).total_seconds())})
        laps = json.loads((Path(laps_dir) / f"laps_{a['id']}.json").read_text())
        out += [{**l, "activity_id": a["id"]} for l in laps]
        prev_end = start + timedelta(seconds=a["elapsed_s"])
    return out


# ---------------------------------------------------------------- cli

def _load(path):
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict) and "time" in data and isinstance(data["time"], dict):
        data = {k: v["data"] for k, v in data.items()}   # MCP stream format
    return data


def main():
    sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles default to cp1252
    args = sys.argv[1:]
    if not args or args[0] not in ("structure", "reps", "splits", "segments", "combine"):
        sys.exit(__doc__)
    cmd, args = args[0], args[1:]
    indoor = "--indoor" in args
    args = [a for a in args if a != "--indoor"]
    if cmd == "combine":
        import os
        laps = combine(_load(args[0]), os.path.dirname(os.path.abspath(args[0])))
        with open(args[1], "w") as f:
            json.dump(laps, f)
        acts = [l["activity_id"] for l in laps if not l.get("gap")]
        out = {"wrote": args[1], "laps": len(acts),
               "activities": list(dict.fromkeys(acts)),
               "gaps_s": [round(l["elapsed_time"]) for l in laps if l.get("gap")]}
    elif cmd == "structure":
        out = structure(_load(args[0]), indoor)
    elif cmd == "segments":
        out = segments(_load(args[0]), args[1], args[2], indoor)
    elif cmd == "reps":
        out = normalize(_load(args[0]), _load(args[1]))
    else:
        split_m = 1000
        if "--split-m" in args:
            k = args.index("--split-m")
            split_m = float(args[k + 1])
            args = args[:k] + args[k + 2:]
        streams = _load(args[2]) if len(args) > 2 else None
        out = splits(_load(args[0]), args[1], streams, split_m, indoor)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
