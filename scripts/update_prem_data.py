#!/usr/bin/env python3
"""Build the static Premier League data feed used by prem.html."""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FPL_BASE = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "ScoreCommand/1.0 (+https://maniary702.github.io/worldcup2026/prem.html)"}


def fetch_json(path: str):
    request = urllib.request.Request(f"{FPL_BASE}/{path}", headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def calculate_table(teams, fixtures):
    rows = {
        team["id"]: {
            "team": team["id"], "played": 0, "won": 0, "drawn": 0, "lost": 0,
            "gf": 0, "ga": 0, "gd": 0, "points": 0, "form": []
        }
        for team in teams
    }
    completed = [f for f in fixtures if f.get("finished") and f.get("team_h_score") is not None]
    completed.sort(key=lambda f: f.get("kickoff_time") or "")
    for match in completed:
        home, away = rows[match["team_h"]], rows[match["team_a"]]
        hs, aas = int(match["team_h_score"]), int(match["team_a_score"])
        for row, scored, conceded in ((home, hs, aas), (away, aas, hs)):
            row["played"] += 1
            row["gf"] += scored
            row["ga"] += conceded
            row["gd"] = row["gf"] - row["ga"]
        if hs > aas:
            home["won"] += 1; home["points"] += 3; away["lost"] += 1
            home["form"].append("W"); away["form"].append("L")
        elif hs < aas:
            away["won"] += 1; away["points"] += 3; home["lost"] += 1
            away["form"].append("W"); home["form"].append("L")
        else:
            home["drawn"] += 1; away["drawn"] += 1
            home["points"] += 1; away["points"] += 1
            home["form"].append("D"); away["form"].append("D")
    name_by_id = {t["id"]: t["name"] for t in teams}
    table = sorted(rows.values(), key=lambda r: (-r["points"], -r["gd"], -r["gf"], name_by_id[r["team"]]))
    for position, row in enumerate(table, 1):
        row["position"] = position
        row["form"] = row["form"][-5:]
    return table


def main():
    bootstrap = fetch_json("bootstrap-static/")
    fixtures_raw = fetch_json("fixtures/")
    teams = [
        {"id": t["id"], "name": t["name"], "short": t["short_name"], "code": t.get("code")}
        for t in bootstrap["teams"]
    ]
    fixtures = [
        {
            "id": f["id"], "event": f.get("event"), "kickoff": f.get("kickoff_time"),
            "home": f["team_h"], "away": f["team_a"],
            "homeScore": f.get("team_h_score"), "awayScore": f.get("team_a_score"),
            "started": bool(f.get("started")), "finished": bool(f.get("finished")),
            "minutes": f.get("minutes", 0)
        }
        for f in fixtures_raw
    ]
    players = [
        {
            "id": p["id"], "team": p["team"], "name": p["web_name"],
            "firstName": p.get("first_name", ""), "lastName": p.get("second_name", ""),
            "position": p["element_type"], "goals": p.get("goals_scored", 0),
            "assists": p.get("assists", 0), "points": p.get("total_points", 0),
            "shirt": p.get("squad_number"), "status": p.get("status", "a")
        }
        for p in bootstrap["elements"]
    ]
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "season": "2026/27",
        "currentEvent": next((e["id"] for e in bootstrap["events"] if e.get("is_current")), None),
        "nextEvent": next((e["id"] for e in bootstrap["events"] if e.get("is_next")), None),
        "teams": teams,
        "fixtures": fixtures,
        "table": calculate_table(teams, fixtures_raw),
        "players": players,
    }
    target = ROOT / "prem_live.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {target.name}: {len(teams)} teams, {len(fixtures)} fixtures, {len(players)} players")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Premier League update failed: {exc}", file=sys.stderr)
        raise
