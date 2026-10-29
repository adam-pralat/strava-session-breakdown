"""Synthetic lap cases for normalize_laps.py. Run: python3 test_normalize_laps.py"""
from normalize_laps import normalize, rep_clock, segments, splits, structure

PLAN = {"rep_distance_m": 400, "reps_min": 8, "reps_max": 10,
        "target_s": [78, 76], "recovery_s": 60}

WARMUP = [{"distance": 3000, "elapsed_time": 900},   # 12 min easy
          {"distance": 1700, "elapsed_time": 420}]   # light progression
STRIDES = [{"distance": 100, "elapsed_time": 16}, {"distance": 100, "elapsed_time": 60},
           {"distance": 100, "elapsed_time": 15}, {"distance": 100, "elapsed_time": 60}]
COOLDOWN = [{"distance": 2000, "elapsed_time": 660}]


def rep(t=77):
    return {"distance": 400, "elapsed_time": t}


def rec():
    return {"distance": 150, "elapsed_time": 60}


def session(main):
    return WARMUP + STRIDES + main + COOLDOWN


def clean(n=9):
    main = []
    for k in range(n):
        main += [rep(78 - k * 0.3), rec()]
    return main


def check(name, laps, count, conf, repaired=None):
    r = normalize(laps, PLAN)
    ok = r["summary"].get("count", 0) == count and r["confidence"] == conf
    if repaired is not None:
        ok = ok and r["summary"]["repaired_reps"] == repaired
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {r['summary']} {r['warnings']}")
    return ok


results = []
results.append(check("clean 9 reps", session(clean()), 9, "high", []))

m = clean()
m[4:5] = [{"distance": 230, "elapsed_time": 44}, {"distance": 170, "elapsed_time": 33}]
results.append(check("rep 3 split mid-rep", session(m), 9, "high", [3]))

m = clean()
m[2:3] = [{"distance": 5, "elapsed_time": 1}, rep(77)]   # sliver folds into recovery
results.append(check("double-press before rep", session(m), 9, "high", []))

m = clean()
m[2:3] = [rep(77), {"distance": 6, "elapsed_time": 1}]   # sliver folds into the rep
results.append(check("double-press after rep", session(m), 9, "high", [2]))

m = clean()
m[6:7] = [{"distance": 360, "elapsed_time": 69}, {"distance": 40, "elapsed_time": 9}]
results.append(check("split near end of rep", session(m), 9, "high", [4]))

m = clean()
m[0:3] = [{"distance": 950, "elapsed_time": 245}]   # forgot to lap rep1+rec+rep2
results.append(check("missed lap press -> low", session(m), 7, "low"))

results.append(check("too few reps -> low", session(clean(6)), 6, "low"))

fast_prog = [{"distance": 2600, "elapsed_time": 746}, {"distance": 1724, "elapsed_time": 361}]
results.append(check("fast warmup progression", fast_prog + clean() + COOLDOWN, 9, "high", []))

m = clean()
m[8:11] = [{"distance": 1200, "elapsed_time": 280}]   # rep5+rec+rep6 as one lap, mid-session
results.append(check("missed press mid-session -> low", session(m + [rep(), rec()]), 8, "low"))

mcp_laps =[{"lap_index": n, "distance_m": l["distance"], "elapsed_time_s": l["elapsed_time"]}
            for n, l in enumerate(session(clean()), 1)]
results.append(check("MCP-style field names", mcp_laps, 9, "high", []))



def check_structure(name, laps, want_pattern, want_breaks):
    sets = [b for b in structure(laps)["blocks"] if b["kind"] == "set"]
    got = [(s["pattern"], s["set_breaks_after"]) for s in sets]
    ok = got == [(want_pattern, want_breaks)]
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got}")
    return ok


def check_splits(name, laps, bout, streams, want_times, conf="high"):
    r = splits(laps, bout, streams)
    got = [s["time_s"] for s in r["splits"]]
    ok = got == want_times and r["confidence"] == conf
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got} {r['confidence']} {r['warnings']}")
    return ok


# The warmup progression (plus the first stride) shows up as its own bout.
results.append(check_structure("structure: clean 9x400", session(clean()),
                               "1×1800m @4:02/km · [2:15] · 9×~400m @3:12/km rec ~60s", ["b1"]))

m = []
for k in range(10):
    m += [rep(75), {"distance": 150, "elapsed_time": 60 if k != 4 else 180}]
results.append(check_structure("structure: 2x5x400 set break", session(m[:-1]),
                               "1×1800m @4:02/km · [2:15] · 5×~400m @3:08/km rec ~60s · "
                               "[3:00] · 5×~400m @3:08/km rec ~60s", ["b1", "b7"]))

PROG_KMS = [215, 210, 205, 200, 195]   # 20' prog, km by km
prog_laps = WARMUP[:1] + [{"distance": 1000, "elapsed_time": t} for t in PROG_KMS] + COOLDOWN
results.append(check_structure("structure: prog lapped per km", prog_laps,
                               "1×5000m @3:25/km", []))
results.append(check_splits("splits: prog lapped per km", prog_laps, "b1", None,
                            [215.0, 210.0, 205.0, 200.0, 195.0]))

one_lap = WARMUP[:1] + [{"distance": 5000, "elapsed_time": 1025}] + COOLDOWN
# Second-by-second stream; 30 s standing still after the warmup lap press, which
# laps leave out.
ts, ds, t, d = [0], [0.0], 0, 0.0
for secs, dist in [(900, 3000), (30, 0)] + [(s, 1000) for s in PROG_KMS] + [(660, 2000)]:
    for _ in range(secs):
        t, d = t + 1, d + dist / secs
        ts.append(t)
        ds.append(d)
streams = {"time": ts, "distance": ds}
results.append(check_splits("splits: prog from streams", one_lap, "b1", streams,
                            [215.0, 210.0, 205.0, 200.0, 195.0]))
results.append(check_splits("splits: no streams -> low", one_lap, "b1", None, [], "low"))

# Standing still after the effort (also missing from laps) mustn't stretch the last km.
ts, ds, t, d = [0], [0.0], 0, 0.0
for secs, dist in [(900, 3000)] + [(s, 1000) for s in PROG_KMS] + [(180, 0), (660, 2000)]:
    for _ in range(secs):
        t, d = t + 1, d + dist / secs
        ts.append(t)
        ds.append(d)
results.append(check_splits("splits: standstill after effort", one_lap, "b1",
                            {"time": ts, "distance": ds}, [215.0, 210.0, 205.0, 200.0, 195.0]))


# Autolap (1 km) left on during long reps: those splits aren't mistakes, so no "*".
PLAN_2K = {"rep_distance_m": 2000, "reps_min": 3, "reps_max": 3,
           "target_s": [400, 390], "recovery_s": 120}


def main_set_plan(laps, plan):
    """What Claude does: name the main set's first and last bout from `structure`."""
    sets = [b for b in structure(laps)["blocks"] if b["kind"] == "set"]
    ids = [b["id"] for b in sets[-1]["bouts"]]
    return {**plan, "bouts": [ids[1] if len(ids) > 3 else ids[0], ids[-1]]}


rec2 = {"distance": 300, "elapsed_time": 120}
m = []
for _ in range(3):   # autolap counts from each lap press
    m += [{"distance": 1000, "elapsed_time": 197}, {"distance": 1000, "elapsed_time": 196}, rec2]
r = normalize(session(m), main_set_plan(session(m), PLAN_2K))
ok = r["summary"]["count"] == 3 and r["summary"]["repaired_reps"] == [] \
    and r["confidence"] == "high"
print(f"{'PASS' if ok else 'FAIL'}  autolap from lap press, 3x2km: {r['summary']}")
results.append(ok)


def autolap_from_start(laps):
    """Split laps wherever cumulative distance crosses a whole km, like a watch
    whose autolap counts from the start of the activity."""
    out, d = [], 0.0
    for l in laps:
        left, t_per_m = l["distance"], l["elapsed_time"] / l["distance"]
        while left > 0:
            piece = min(left, 1000 - d % 1000)
            out.append({"distance": piece, "elapsed_time": piece * t_per_m})
            d, left = d + piece, left - piece
    return out


main_2k = []
for _ in range(3):
    main_2k += [{"distance": 2000, "elapsed_time": 393}, rec2]
laps_2k = autolap_from_start(session(main_2k))
r = normalize(laps_2k, main_set_plan(laps_2k, PLAN_2K))
ok = r["summary"]["count"] == 3 and r["summary"]["repaired_reps"] == [] \
    and r["confidence"] == "high"
print(f"{'PASS' if ok else 'FAIL'}  autolap from activity start, 3x2km: {r['summary']}")
results.append(ok)

m = clean()
m[4:5] = [{"distance": 230, "elapsed_time": 44}, {"distance": 170, "elapsed_time": 33}]
results.append(check("real split still starred with autolap warmup",
                     autolap_from_start(WARMUP) + STRIDES + m + COOLDOWN, 9, "high", [3]))

# 20' prog with autolap from activity start: laps are offset, so splits need streams.
offset_prog = autolap_from_start([{"distance": 2616, "elapsed_time": 780}, {"distance": 5000, "elapsed_time": 1025}]
                                 + COOLDOWN)
prog_bout = next(b for blk in structure(offset_prog)["blocks"] if blk["kind"] == "set"
                 for b in blk["bouts"])
results.append(check_splits("offset autolap prog, no streams -> low", offset_prog,
                            prog_bout["id"], None, [], "low"))

# Indoors: the accelerometer reads 15% short on every lap.
def scaled(laps, f):
    return [{**l, "distance": l["distance"] * f} for l in laps]


def main_bouts(laps, indoor):
    s = [b for b in structure(laps, indoor)["blocks"] if b["kind"] == "set"][-1]
    ids = [b["id"] for b in s["bouts"]]
    return [ids[1] if len(ids) > 9 else ids[0], ids[-1]]   # skip the warmup prog


short = scaled(session(clean()), 0.85)
shorter = scaled(session(clean()), 0.8)   # 320 m reps: below outdoor's 340 m minimum
r = normalize(shorter, {**PLAN, "bouts": main_bouts(shorter, False)})
ok = r["confidence"] == "low" and any("didn't fit a rep" in w for w in r["warnings"])
print(f"{'PASS' if ok else 'FAIL'}  20% short, outdoor mode -> low, not a silent miscount: "
      f"{r['summary'].get('count')} {r['warnings']}")
results.append(ok)

r = normalize(short, {**PLAN, "bouts": main_bouts(short, True), "indoor": True})
ok = r["summary"].get("count") == 9 and r["confidence"] == "high"
print(f"{'PASS' if ok else 'FAIL'}  15% short, indoor mode -> 9 reps by time: {r['summary']}")
results.append(ok)

m = clean()
m[4:5] = [{"distance": 230 * 0.85, "elapsed_time": 44}, {"distance": 170 * 0.85, "elapsed_time": 33}]
laps_in = scaled(WARMUP + STRIDES, 0.85) + m + scaled(COOLDOWN, 0.85)
r = normalize(laps_in, {**PLAN, "bouts": main_bouts(laps_in, True), "indoor": True})
ok = r["summary"].get("count") == 9 and r["summary"]["repaired_reps"] == [3] \
    and r["confidence"] == "high"
print(f"{'PASS' if ok else 'FAIL'}  indoor split rep repaired by time: {r['summary']}")
results.append(ok)

pat = [b["pattern"] for b in structure(short, True)["blocks"] if b["kind"] == "set"]
ok = pat and "×~1:17 rec ~60s" in pat[-1] and "m @" not in pat[-1]
print(f"{'PASS' if ok else 'FAIL'}  indoor structure pattern is in times: {pat}")
results.append(bool(ok))

r = splits(short, "b1", None, indoor=True)
ok = r["confidence"] == "low" and r["splits"] == []
print(f"{'PASS' if ok else 'FAIL'}  indoor splits refuse: {r['warnings']}")
results.append(ok)

# A session saved as several activities: WU+set 1, then set 2+CD.
import json  # noqa: E402
import tempfile  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402
from normalize_laps import combine  # noqa: E402


def as_activities(parts, gap_s=150):
    d = Path(tempfile.mkdtemp())
    session, start = [], datetime(2026, 10, 28, 17, 0)
    for k, part in enumerate(parts):
        (d / f"laps_A{k + 1}.json").write_text(json.dumps(part))
        el = sum(l["elapsed_time"] for l in part)
        session.append({"id": f"A{k + 1}", "start": start.isoformat(), "elapsed_s": el})
        start += timedelta(seconds=el + gap_s)
    return combine(session, d)



def rep_ids(laps):
    """Main-set bout ids: the last set's bouts of rep length (drops a warmup prog)."""
    bouts = [b for b in structure(laps)["blocks"] if b["kind"] == "set"][-1]["bouts"]
    med = sorted(b["distance_m"] for b in bouts)[len(bouts) // 2]
    ids = [b["id"] for b in bouts if b["distance_m"] <= 1.5 * med]
    return ids[0], ids[-1]


m = clean(10)
two = as_activities([WARMUP + STRIDES + m[:9], m[10:] + COOLDOWN])   # rec after rep 5 lost
blk = [b for b in structure(two)["blocks"] if b["kind"] == "set"][-1]
ids = [b["id"] for b in blk["bouts"]]
r = normalize(two, {**PLAN, "bouts": [ids[1], ids[-1]]})
by = r["summary"].get("by_activity", {})
seg = segments(two, ids[1], ids[-1])
ok = r["summary"]["count"] == 10 and r["confidence"] == "high" \
    and by.get("A1", {}).get("reps") == [1, 2, 3, 4, 5] \
    and by.get("A2", {}).get("reps") == [6, 7, 8, 9, 10] \
    and blk["set_breaks_after"] == [ids[0], ids[5]] \
    and seg["activities"] == {"A1": ["warmup", "main"], "A2": ["main", "cooldown"]}
print(f"{'PASS' if ok else 'FAIL'}  set split over 2 activities: {blk['pattern']} "
      f"{ {a: v['reps'] for a, v in by.items()} } {seg.get('activities')}")
results.append(ok)

three = as_activities([WARMUP, STRIDES + clean(), COOLDOWN])
first, last = rep_ids(three)
r = normalize(three, {**PLAN, "bouts": [first, last]})
seg = segments(three, first, last)
ok = r["summary"]["count"] == 9 and r["confidence"] == "high" \
    and seg["activities"] == {"A1": ["warmup"],
                              "A2": ["warmup", "main", "cooldown"],   # strides, last jog
                              "A3": ["cooldown"]} \
    and seg["cooldown"]["moving"] == "12:00" \
    and seg["cooldown"]["by_activity"]["A3"]["moving"] == "11:00"
print(f"{'PASS' if ok else 'FAIL'}  WU / main / CD as 3 activities: {seg.get('activities')} "
      f"CD {seg['cooldown']['moving']}")
results.append(ok)

got = [rep_clock(58.3), rep_clock(59.96), rep_clock(70.3), rep_clock(152.5),
       rep_clock(119.96), rep_clock(172, 0), rep_clock(78, 0), rep_clock(3725.4)]
ok = got == ["58.3", "60.0", "1:10.3", "2:32.5", "2:00.0", "2:52", "1:18", "62:05.4"]
print(f"{'PASS' if ok else 'FAIL'}  rep_clock m:ss over a minute: {got}")
results.append(ok)

# WU/CD use moving time, so standing around (drills, after the last rep) drops out.
def lap(d, elapsed, moving):
    return {"distance": d, "elapsed_time": elapsed, "moving_time": moving}


wu = [lap(3000, 900, 880), lap(1700, 420, 420),            # easy, prog (b1)
      lap(300, 200, 100),                                   # drills
      lap(100, 16, 16), lap(100, 60, 50), lap(100, 15, 15),  # strides (b2, b3)
      lap(100, 60, 50)]                                     # jog to the start
cd = [lap(2000, 900, 660)]
seg = segments(wu + clean() + cd, "b4", "b12")
got = (seg["warmup"]["moving"],
       [(p["kind"], p.get("moving") or p["time"]) for p in seg["warmup"]["pieces"]],
       seg["warmup"]["pieces"][-1].get("count"), seg["cooldown"]["moving"])
ok = got == ("25:31", [("easy", "14:40"), ("effort", "7:00"), ("easy", "1:40"),
                       ("strides", "2:11")], 2, "12:00")
print(f"{'PASS' if ok else 'FAIL'}  segments: moving-time WU/CD with pieces: {got}")
results.append(ok)

print(f"\n{sum(results)}/{len(results)} passed")
