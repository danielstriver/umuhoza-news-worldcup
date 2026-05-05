"""
Sofascore Football Competitions Scraper
========================================
Scrapes football tournament data from Sofascore.

Supported competitions:
  ucl  = UEFA Champions League  (unique_tournament_id = 7)

Output layout:
  output/football/
    competitions.json              <- list of all supported competitions
    {slug}/
      seasons.json                 <- all available seasons
      {year}/
        standings.json
        cuptree.json
        matches.json
        events/
          {eid}_{slug}.json        <- stats + lineups + incidents + h2h

Usage:
  python football_scraper.py                           # UCL latest season only
  python football_scraper.py --seasons all             # all UCL seasons
  python football_scraper.py --seasons 76953           # specific season ID
  python football_scraper.py --seasons 76953,61644     # multiple seasons
  python football_scraper.py --max-events 5            # quick test
  python football_scraper.py --no-headless             # watch the browser
"""

import argparse
import json
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

BASE_API  = "https://api.sofascore.com/api/v1"
OUTPUT_DIR = Path(__file__).parent / "output" / "football"

COMPETITIONS = {
    "ucl": {
        "id": 7,
        "name": "UEFA Champions League",
        "slug": "ucl",
        "country": "Europe",
        "entry_url": (
            "https://www.sofascore.com/football/tournament/"
            "europe/uefa-champions-league/7"
        ),
    },
}


# ── year helpers ─────────────────────────────────────────────────────────────
def normalize_year(raw: str) -> str:
    """
    Normalize Sofascore season year strings to a consistent "YYYY-YY" label.
    Examples:  "24/25" -> "2024-25",  "2023/24" -> "2023-24",  "2024" -> "2024"
    """
    raw = (raw or "").strip()
    # "24/25" (two-digit slash)
    m = re.match(r'^(\d{2})[/-](\d{2})$', raw)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return f"20{a:02d}-{b:02d}"
    # "2024/25"
    m = re.match(r'^(\d{4})[/-](\d{2})$', raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # "2024/2025"
    m = re.match(r'^(\d{4})[/-](\d{4})$', raw)
    if m:
        return f"{m.group(1)}-{m.group(2)[2:]}"
    return raw.replace('/', '-') if raw else "unknown"


# ── fetch helper ──────────────────────────────────────────────────────────────
def fetch_json(page: Page, path: str, retries: int = 3):
    url = f"{BASE_API}{path}"
    for attempt in range(retries):
        try:
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
        except Exception as exc:
            # "Execution context was destroyed" or similar — wait and retry
            if attempt < retries - 1:
                time.sleep(3 ** attempt)
                continue
            print(f"  [WARN] page.evaluate failed for {path}: {type(exc).__name__}")
            return None
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
    print(f"  [SAVE] output/football/{Path(*parts)}")


# ── season discovery ─────────────────────────────────────────────────────────
def fetch_seasons(page: Page, comp: dict) -> list:
    tid = comp["id"]
    print(f"\n[seasons] tournament={tid} ({comp['name']})")
    data = fetch_json(page, f"/unique-tournament/{tid}/seasons")
    if not data:
        print(f"  [WARN] Could not fetch seasons")
        return []
    seasons = data.get("seasons", [])
    for s in seasons:
        raw_year = s.get("year", "")
        s["year_label"] = normalize_year(raw_year) if raw_year else normalize_year("")
    print(f"  Found {len(seasons)} seasons")
    return seasons


# ── season-level scrapers ────────────────────────────────────────────────────
def scrape_standings(page: Page, tid: int, sid: int, year: str, slug: str):
    print(f"\n[standings]  season={sid}")
    data = fetch_json(page, f"/unique-tournament/{tid}/season/{sid}/standings/total")
    if data:
        save(data, slug, year, "standings.json")
    return data


def scrape_cuptree(page: Page, tid: int, sid: int, year: str, slug: str):
    print(f"[cuptree]    season={sid}")
    data = fetch_json(page, f"/unique-tournament/{tid}/season/{sid}/cuptrees")
    if data:
        save(data, slug, year, "cuptree.json")
    return data


def scrape_matches(page: Page, tid: int, sid: int, year: str, slug: str) -> list:
    print(f"[matches]    season={sid}")
    all_events: list = []

    for direction in ("last", "next"):
        page_num = 0
        while True:
            path = f"/unique-tournament/{tid}/season/{sid}/events/{direction}/{page_num}"
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

    seen: set = set()
    unique = [e for e in all_events if not (e["id"] in seen or seen.add(e["id"]))]

    save(
        {"season_id": sid, "year": year, "total": len(unique), "events": unique},
        slug, year, "matches.json",
    )
    print(f"  Total events: {len(unique)}")
    return unique


# ── event-level scrapers ─────────────────────────────────────────────────────
def scrape_event(page: Page, event: dict, year: str, slug: str):
    eid     = event["id"]
    home    = event.get("homeTeam", {}).get("name", "?")
    away    = event.get("awayTeam", {}).get("name", "?")
    ev_slug = event.get("slug", f"match-{eid}")
    status  = event.get("status", {}).get("type", "")

    if status not in ("finished", "inprogress", "postponed", "canceled"):
        return

    print(f"    [{eid}]  {home} vs {away}  [{status}]")

    detail: dict = {
        "event_id": eid,
        "slug": ev_slug,
        "home": home,
        "away": away,
        "status": status,
    }

    for endpoint in ("statistics", "lineups", "incidents"):
        d = fetch_json(page, f"/event/{eid}/{endpoint}")
        if d:
            detail[endpoint] = d
        time.sleep(0.3)

    # H2H — previous meetings between the two teams
    h2h = fetch_json(page, f"/event/{eid}/h2h/events")
    if h2h:
        detail["h2h"] = h2h
    time.sleep(0.3)

    save(detail, slug, year, "events", f"{eid}_{ev_slug}.json")


# ── season orchestration ─────────────────────────────────────────────────────
def scrape_season(page: Page, comp: dict, season: dict, max_events: int = 0):
    tid  = comp["id"]
    slug = comp["slug"]
    sid  = season["id"]
    year = season.get("year_label", str(sid))

    print(f"\n{'='*60}")
    print(f"  {comp['name']} {year}  (season_id={sid})")
    print(f"{'='*60}")

    scrape_standings(page, tid, sid, year, slug)
    time.sleep(0.5)
    scrape_cuptree(page, tid, sid, year, slug)
    time.sleep(0.5)

    events = scrape_matches(page, tid, sid, year, slug)
    if max_events:
        events = events[:max_events]

    print(f"\n[event details]  {len(events)} events")
    for ev in events:
        try:
            scrape_event(page, ev, year, slug)
        except Exception as exc:
            print(f"    [ERR] event {ev.get('id')}: {exc}")
        time.sleep(0.4)


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Sofascore Football scraper")
    ap.add_argument(
        "--comp", default="ucl",
        help=f"Competition slug. Available: {', '.join(COMPETITIONS)}. Default: ucl",
    )
    ap.add_argument(
        "--seasons", default="",
        help=(
            "Comma-separated season IDs to scrape, or 'all' for every season. "
            "Default: latest season only."
        ),
    )
    ap.add_argument(
        "--max-events", type=int, default=0,
        help="Max events per season for match-detail scraping (0 = all).",
    )
    ap.add_argument("--headless", dest="headless", action="store_true", default=True)
    ap.add_argument("--no-headless", dest="headless", action="store_false")
    args = ap.parse_args()

    comp = COMPETITIONS.get(args.comp)
    if not comp:
        print(f"Unknown competition: {args.comp}. Available: {', '.join(COMPETITIONS)}")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Save competitions manifest (used by the dashboard to populate competition list)
    (OUTPUT_DIR / "competitions.json").write_text(
        json.dumps({"competitions": list(COMPETITIONS.values())}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Scraping {comp['name']}")
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

        print(f"\n[init] Loading {comp['name']} page to acquire session cookies ...")
        for nav_attempt in range(3):
            try:
                page.goto(comp["entry_url"], wait_until="domcontentloaded", timeout=60_000)
                time.sleep(5)
                print("  Title:", page.title())
                break
            except Exception as exc:
                print(f"  [WARN] Navigation attempt {nav_attempt+1} failed: {type(exc).__name__}")
                if nav_attempt < 2:
                    print("  Retrying in 10s ...")
                    time.sleep(10)
                    try:
                        page.goto("about:blank", timeout=5_000)
                    except Exception:
                        pass

        all_seasons = fetch_seasons(page, comp)
        if not all_seasons:
            print("No seasons found, exiting")
            browser.close()
            return

        # Save seasons manifest
        comp_dir = OUTPUT_DIR / comp["slug"]
        comp_dir.mkdir(parents=True, exist_ok=True)
        (comp_dir / "seasons.json").write_text(
            json.dumps(
                {"unique_tournament_id": comp["id"], "seasons": all_seasons},
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # Determine which seasons to scrape
        seasons_arg = args.seasons.strip()
        if seasons_arg == "all":
            seasons_to_scrape = all_seasons
        elif seasons_arg:
            filter_ids = {int(x.strip()) for x in seasons_arg.split(",") if x.strip()}
            seasons_to_scrape = [s for s in all_seasons if s["id"] in filter_ids]
        else:
            # Default: latest season only
            seasons_to_scrape = all_seasons[:1]

        print(f"Scraping {len(seasons_to_scrape)} season(s): "
              f"{[s.get('year_label', s['id']) for s in seasons_to_scrape]}")

        for season in seasons_to_scrape:
            try:
                scrape_season(page, comp, season, max_events=args.max_events)
            except Exception as exc:
                print(f"  [ERROR] Season {season.get('year_label', season['id'])}: {exc}")

        browser.close()

    print(f"\n[DONE] Data saved to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
