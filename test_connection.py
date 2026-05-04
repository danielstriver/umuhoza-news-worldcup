"""
Quick connectivity test + tournament-ID discovery.
Intercepts real API calls while loading the World Cup page,
then confirms the correct uniqueTournamentId and fetches all seasons.
"""

import json, time, re
from playwright.sync_api import sync_playwright

ENTRY_URL = "https://www.sofascore.com/football/tournament/world/world-championship/16"
BASE_API  = "https://api.sofascore.com/api/v1"

captured_api_urls = []

def on_response(response):
    url = response.url
    if "api.sofascore.com/api/v1" in url:
        captured_api_urls.append(url)

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = ctx.new_page()
        page.on("response", on_response)

        print("Opening World Cup page ...")
        page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)
        time.sleep(8)  # let XHR/fetch settle
        print("Title:", page.title())

        # Show intercepted API calls
        print("\nIntercepted API calls:")
        seen = set()
        for u in captured_api_urls:
            if u not in seen:
                seen.add(u)
                print(" ", u)

        # Extract tournament IDs from intercepted URLs
        tournament_ids = set()
        for u in captured_api_urls:
            m = re.search(r"/unique-tournament/(\d+)", u)
            if m:
                tournament_ids.add(int(m.group(1)))
            m = re.search(r"/tournament/(\d+)/season", u)
            if m:
                tournament_ids.add(int(m.group(1)))

        print(f"\nDiscovered tournament IDs from network: {tournament_ids}")

        # Try each discovered ID to fetch seasons
        for tid in sorted(tournament_ids):
            print(f"\nFetching /tournament/{tid}/seasons ...")
            data = page.evaluate(
                """async (url) => {
                    const r = await fetch(url, { headers: { Accept: 'application/json' } });
                    return r.ok ? await r.json() : { error: r.status };
                }""",
                f"{BASE_API}/tournament/{tid}/seasons",
            )
            if isinstance(data, dict) and "error" in data:
                print("  ERROR:", data)
                continue
            seasons = data.get("seasons", [])
            print(f"  Found {len(seasons)} seasons:")
            for s in seasons[:5]:
                print(f"    id={s['id']:>6}  year={s.get('year','?'):>9}  name={s.get('name','')}")
            if len(seasons) > 5:
                print(f"    ... and {len(seasons)-5} more")

            with open("seasons_test.json", "w", encoding="utf-8") as f:
                json.dump({"tournament_id": tid, **data}, f, indent=2, ensure_ascii=False)
            print(f"  Saved => seasons_test.json  (tournament_id={tid})")

        browser.close()

if __name__ == "__main__":
    main()
