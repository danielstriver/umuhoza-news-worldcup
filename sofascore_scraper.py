"""
Sofascore FIFA World Cup Scraper
=================================
Tournament : FIFA World Cup  (uniqueTournamentId = 16)
API prefix : /unique-tournament/16/  (confirmed via browser intercept)
Strategy   : Playwright injects fetch() from inside the browser so all
             Cloudflare cookies / session tokens are automatically present.

Confirmed endpoints
-------------------
  /unique-tournament/16/seasons                          -> 23 seasons (1930-2026)
  /unique-tournament/16/season/{sid}/standings/total    -> group tables
  /unique-tournament/16/season/{sid}/cuptrees           -> knockout bracket
  /unique-tournament/16/season/{sid}/events/last/{page} -> events (30/page, hasNextPage)
  /unique-tournament/16/season/{sid}/events/next/{page} -> future events
  /event/{eid}/statistics                               -> match statistics
  /event/{eid}/lineups                                  -> confirmed lineups
  /event/{eid}/incidents                                -> goals, cards, subs

Output layout
-------------
  output/
    seasons.json                      <- all 23 seasons
    {year}/
      standings.json                  <- group standings
      cuptree.json                    <- knockout bracket
      matches.json                    <- every event in the season
      events/
        {eid}_{slug}.json             <- stats + lineups + incidents per match

Usage
-----
  pip install playwright
  playwright install chromium
  python sofascore_scraper.py                          # all 23 seasons
  python sofascore_scraper.py --seasons 41087,15586    # 2022 + 2018 only
  python sofascore_scraper.py --seasons 41087 --max-events 5   # quick test
  python sofascore_scraper.py --no-headless            # watch the browser
"""

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

# ── constants ────────────────────────────────────────────────────────────────
UNIQUE_TOURNAMENT_ID = 16
BASE_API = "https://api.sofascore.com/api/v1"
ENTRY_URL = (
    "https://www.sofascore.com/football/tournament/world/"
    f"world-championship/{UNIQUE_TOURNAMENT_ID}"
)
OUTPUT_DIR = Path(__file__).parent / "output"

# All 23 FIFA World Cup seasons scraped from page SSR data (2026 → 1930)
KNOWN_SEASONS = [
    {"id": 58210, "year": "2026", "name": "World Cup 2026"},
    {"id": 41087, "year": "2022", "name": "World Cup 2022"},
    {"id": 15586, "year": "2018", "name": "World Cup 2018"},
    {"id":  7528, "year": "2014", "name": "World Cup 2014"},
    {"id":  2531, "year": "2010", "name": "World Cup 2010"},
    {"id":    16, "year": "2006", "name": "World Cup 2006"},
    {"id":  2636, "year": "2002", "name": "World Cup 2002"},
    {"id":  1151, "year": "1998", "name": "World Cup 1998"},
    {"id": 17571, "year": "1994", "name": "World Cup 1994"},
    {"id": 17570, "year": "1990", "name": "World Cup 1990"},
    {"id": 17569, "year": "1986", "name": "World Cup 1986"},
    {"id": 17568, "year": "1982", "name": "World Cup 1982"},
    {"id": 17567, "year": "1978", "name": "World Cup 1978"},
    {"id": 17566, "year": "1974", "name": "World Cup 1974"},
    {"id": 17565, "year": "1970", "name": "World Cup 1970"},
    {"id": 17564, "year": "1966", "name": "World Cup 1966"},
    {"id": 17563, "year": "1962", "name": "World Cup 1962"},
    {"id": 17562, "year": "1958", "name": "World Cup 1958"},
    {"id": 17561, "year": "1954", "name": "World Cup 1954"},
    {"id": 40714, "year": "1950", "name": "World Cup 1950"},
    {"id": 17560, "year": "1938", "name": "World Cup 1938"},
    {"id": 17559, "year": "1934", "name": "World Cup 1934"},
    {"id": 40712, "year": "1930", "name": "World Cup 1930"},
]


# ── helpers ──────────────────────────────────────────────────────────────────
def fetch_json(page: Page, path: str, retries: int = 3):
    url = f"{BASE_API}{path}"
    for attempt in range(retries):
        result = page.evaluate(
            """async (url) => {
                try {
                    const r = await fetch(url, {
                        headers: { 'Accept': 'application/json, */*' }
                    });
                    if (!r.ok) return { __status: r.status };
                    return await r.json();
                } catch (e) {
                    return { __error: e.toString() };
                }
            }""",
            url,
        )
        if isinstance(result, dict) and ("__error" in result or "__status" in result):
            code = result.get("__status", 0)
            if code == 404:
                return None
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            print(f"  [WARN] {path} => {result}")
            return None
        return result
    return None


def save(data, *parts: str):
    dest = OUTPUT_DIR.joinpath(*parts)
    dest.parent.mkdir(parents=True, exist_ok=True)
    path = dest if str(dest).endswith(".json") else dest.with_suffix(".json")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [SAVE] output/{Path(*parts)}")


# ── season-level scrapers ────────────────────────────────────────────────────
def scrape_standings(page: Page, sid: int, year: str):
    print(f"\n[standings]  season={sid}")
    data = fetch_json(page, f"/unique-tournament/{UNIQUE_TOURNAMENT_ID}/season/{sid}/standings/total")
    if data:
        save(data, year, "standings.json")
    return data


def scrape_cuptree(page: Page, sid: int, year: str):
    print(f"[cuptree]    season={sid}")
    data = fetch_json(page, f"/unique-tournament/{UNIQUE_TOURNAMENT_ID}/season/{sid}/cuptrees")
    if data:
        save(data, year, "cuptree.json")
    return data


def scrape_matches(page: Page, sid: int, year: str) -> list:
    print(f"[matches]    season={sid}")
    all_events: list = []

    for direction in ("last", "next"):
        page_num = 0
        while True:
            path = f"/unique-tournament/{UNIQUE_TOURNAMENT_ID}/season/{sid}/events/{direction}/{page_num}"
            data = fetch_json(page, path)
            if not data:
                break
            events = data.get("events", [])
            if not events:
                break
            all_events.extend(events)
            has_next = data.get("hasNextPage", False)
            print(f"    {direction}/page{page_num}: +{len(events)} events  hasNextPage={has_next}")
            if not has_next:
                break
            page_num += 1
            time.sleep(0.5)

    # de-duplicate
    seen: set = set()
    unique = [e for e in all_events if not (e["id"] in seen or seen.add(e["id"]))]

    save(
        {"season_id": sid, "year": year, "total": len(unique), "events": unique},
        year, "matches.json",
    )
    print(f"  Total events: {len(unique)}")
    return unique


# ── event-level scrapers ─────────────────────────────────────────────────────
def scrape_event(page: Page, event: dict, year: str):
    eid = event["id"]
    home = event.get("homeTeam", {}).get("name", "?")
    away = event.get("awayTeam", {}).get("name", "?")
    slug = event.get("slug", f"match-{eid}")
    status = event.get("status", {}).get("type", "")

    if status not in ("finished", "inprogress", "postponed", "canceled"):
        return

    print(f"    [{eid}]  {home} vs {away}  [{status}]")

    detail: dict = {
        "event_id": eid,
        "slug": slug,
        "home": home,
        "away": away,
        "status": status,
    }

    for endpoint in ("statistics", "lineups", "incidents"):
        d = fetch_json(page, f"/event/{eid}/{endpoint}")
        if d:
            detail[endpoint] = d
        time.sleep(0.3)

    save(detail, year, "events", f"{eid}_{slug}.json")


# ── season orchestration ──────────────────────────────────────────────────────
def scrape_season(page: Page, season: dict, max_events: int = 0):
    sid  = season["id"]
    year = season["year"]
    print(f"\n{'='*60}")
    print(f"  {season['name']}  (season_id={sid})")
    print(f"{'='*60}")

    scrape_standings(page, sid, year)
    time.sleep(0.5)
    scrape_cuptree(page, sid, year)
    time.sleep(0.5)

    events = scrape_matches(page, sid, year)

    if max_events:
        events = events[:max_events]

    print(f"\n[event details]  {len(events)} events")
    for ev in events:
        try:
            scrape_event(page, ev, year)
        except Exception as exc:
            print(f"    [ERR] event {ev.get('id')}: {exc}")
        time.sleep(0.4)


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Sofascore FIFA World Cup scraper")
    ap.add_argument(
        "--seasons",
        default="",
        help="Comma-separated season IDs to scrape (e.g. 41087,15586). Default: all.",
    )
    ap.add_argument(
        "--max-events",
        type=int,
        default=0,
        help="Max events per season for match-detail scraping (0 = all).",
    )
    ap.add_argument("--headless", dest="headless", action="store_true", default=True)
    ap.add_argument("--no-headless", dest="headless", action="store_false")
    args = ap.parse_args()

    filter_ids = (
        {int(x.strip()) for x in args.seasons.split(",") if x.strip()}
        if args.seasons
        else set()
    )

    seasons = (
        [s for s in KNOWN_SEASONS if s["id"] in filter_ids]
        if filter_ids
        else KNOWN_SEASONS
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    save(
        {"unique_tournament_id": UNIQUE_TOURNAMENT_ID, "seasons": KNOWN_SEASONS},
        "seasons.json",
    )

    print(f"Scraping {len(seasons)} season(s): {[s['year'] for s in seasons]}")
    print(f"Output => {OUTPUT_DIR}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=args.headless,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="Europe/London",
        )
        page = ctx.new_page()

        print("\n[init] Loading Sofascore to acquire session cookies ...")
        try:
            page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)
            time.sleep(5)
            print("  Title:", page.title())
        except Exception as exc:
            print(f"  [WARN] Navigation partial ({exc}), continuing with cookies set so far")

        for season in seasons:
            try:
                scrape_season(page, season, max_events=args.max_events)
            except Exception as exc:
                print(f"  [ERROR] Season {season['year']}: {exc}")

        browser.close()

    print(f"\n[DONE] Data saved to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
