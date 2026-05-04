"""
Offline data explorer - reads the JSON files saved by sofascore_scraper.py
and prints summaries: seasons list, group standings, knockout tree, match stats.

Usage:
    python explore_output.py                    # list all seasons
    python explore_output.py --season 2022      # standings + knockout + first 5 matches
    python explore_output.py --season 2022 --event 12345678   # single event detail
"""

import argparse
import json
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUTPUT = Path(__file__).parent / "output"


def load(filepath: Path):
    if filepath.exists():
        return json.loads(filepath.read_text(encoding="utf-8"))
    return None


def show_seasons():
    data = load(OUTPUT / "seasons.json")
    if not data:
        print("No seasons.json found. Run sofascore_scraper.py first.")
        return
    print(f"\nWorld Cup seasons ({data['tournament_id']=}):\n")
    for s in data["seasons"]:
        year = s.get("year") or s.get("name")
        print(f"  id={s['id']:>6}  year={year:>9}  name={s.get('name','')}")


def show_standings(season_label: str):
    data = load(OUTPUT / season_label / "standings.json")
    if not data:
        print(f"No standings for season '{season_label}'")
        return

    rows = data.get("standings", [])
    if not rows:
        rows = [data]  # some seasons wrap differently

    for group_data in rows:
        group_name = group_data.get("name", "")
        print(f"\n  -- {group_name or 'Standings'} --")
        print(f"  {'#':>2}  {'Team':<28}  {'P':>2}  {'W':>2}  {'D':>2}  {'L':>2}  {'GF':>3}  {'GA':>3}  {'Pts':>3}")
        for row in group_data.get("rows", []):
            t = row.get("team", {})
            print(
                f"  {row.get('position',0):>2}  {t.get('name','?'):<28}"
                f"  {row.get('matches',0):>2}  {row.get('wins',0):>2}"
                f"  {row.get('draws',0):>2}  {row.get('losses',0):>2}"
                f"  {row.get('scoresFor',0):>3}  {row.get('scoresAgainst',0):>3}"
                f"  {row.get('points',0):>3}"
            )


def show_cuptree(season_label: str):
    data = load(OUTPUT / season_label / "cuptree.json")
    if not data:
        print(f"No cuptree for season '{season_label}'")
        return

    for cup in data.get("cupTrees", []):
        print(f"\n  Cup: {cup.get('name', '')}")
        for rnd in cup.get("rounds", []):
            rname = rnd.get("description") or rnd.get("slug", "?")
            print(f"\n  -- {rname} --")
            for block in rnd.get("blocks", []):
                parts = block.get("participants", [])
                t1 = parts[0].get("team", {}).get("name", "TBD") if len(parts) > 0 else "TBD"
                t2 = parts[1].get("team", {}).get("name", "TBD") if len(parts) > 1 else "TBD"
                result = block.get("result", "vs")
                raw_events = block.get("events", [])
                eids = [str(ev) if isinstance(ev, int) else str(ev.get("id", "")) for ev in raw_events]
                eid_str = ",".join(eids) if eids else "-"
                print(f"    {t1:<25} {result} {t2:<25}  (event {eid_str})")


def show_matches(season_label: str, limit: int = 5):
    data = load(OUTPUT / season_label / "matches.json")
    if not data:
        print(f"No matches for season '{season_label}'")
        return

    events = data.get("events", [])
    print(f"\n  {len(events)} total events — showing first {limit}:\n")
    for ev in events[:limit]:
        home = ev.get("homeTeam", {}).get("name", "?")
        away = ev.get("awayTeam", {}).get("name", "?")
        hs = ev.get("homeScore", {}).get("current", "")
        as_ = ev.get("awayScore", {}).get("current", "")
        ts = ev.get("startTimestamp", 0)
        import datetime
        dt = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else "?"
        status = ev.get("status", {}).get("type", "")
        eid = ev.get("id")
        print(f"  [{eid}]  {dt}  {home} {hs}-{as_} {away}  [{status}]")


def show_event(season_label: str, event_id: int):
    ev_dir = OUTPUT / season_label / "events"
    matches = list(ev_dir.glob(f"{event_id}_*.json")) if ev_dir.exists() else []
    if not matches:
        print(f"No saved detail for event {event_id} in season '{season_label}'")
        return
    data = load(matches[0])
    print(f"\n  Event {event_id} detail keys: {list(data.keys())}\n")

    stats = data.get("statistics", {}).get("statistics", [])
    for period in stats:
        print(f"\n  Period: {period.get('period','ALL')}")
        for grp in period.get("groups", []):
            print(f"    {grp['groupName']}")
            for item in grp.get("statisticsItems", []):
                print(f"      {item['name']:<30}  home={item.get('home','')}  away={item.get('away','')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="", help="Season label, e.g. 2022 or 2026")
    ap.add_argument("--event", type=int, default=0, help="Event ID for match detail")
    args = ap.parse_args()

    if not args.season:
        show_seasons()
        return

    label = args.season
    print(f"\n{'='*60}")
    print(f"  Season: {label}")
    print(f"{'='*60}")

    if args.event:
        show_event(label, args.event)
        return

    show_standings(label)
    show_cuptree(label)
    show_matches(label, limit=10)


if __name__ == "__main__":
    main()
