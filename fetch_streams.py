#!/usr/bin/env python3
"""Download an activity's time and distance streams for km splits.

Usage:
    python3 fetch_streams.py <activity_id>

Writes tmp/streams_<id>.json as {"time": [...], "distance": [...]}. Streams are
thousands of points, too many to pass through Claude, so this calls Strava directly
with the access token the Strava MCP server keeps in ~/.strava-mcp-tokens.json.
It never refreshes the token (that would rotate it under the server); if it has
expired, call any Strava MCP tool first and retry.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

TOKEN_FILE = Path.home() / ".strava-mcp-tokens.json"


def main():
    activity_id = int(sys.argv[1])
    tokens = json.loads(TOKEN_FILE.read_text())
    if tokens.get("expires_at", 0) < time.time() + 60:
        sys.exit("Strava access token expired; call any Strava MCP tool, then retry.")

    req = urllib.request.Request(
        f"https://www.strava.com/api/v3/activities/{activity_id}/streams"
        "?keys=time,distance&key_by_type=true",
        headers={"Authorization": f"Bearer {tokens['access_token']}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)

    out = {"time": data["time"]["data"], "distance": data["distance"]["data"]}
    path = Path(__file__).parent / "tmp" / f"streams_{activity_id}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out))
    print(f"Wrote {path.name}: {len(out['time'])} points, "
          f"{out['distance'][-1]:.0f} m, {out['time'][-1]} s")


if __name__ == "__main__":
    main()
