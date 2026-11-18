"""Opt-in full-template browser smoke test; no downloads or running scheduler.

Run from the repository root with a Python environment containing Playwright.
Uses real FastAPI routes and recorded source payloads; screenshots go to tmp/.
"""
import copy
import gc
import html
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect


def main():
    with tempfile.TemporaryDirectory(prefix="au-browser-") as temp:
        os.environ["DB_FILE"] = str(Path(temp) / "panel.db")
        os.environ["DATA_FILE"] = str(Path(temp) / "data.json")
        os.environ["SCHEDULE_FILE"] = str(Path(temp) / "schedule.json")
        from app import db
        from app.core import animeunity as au, animeunity_catalog as catalog
        from app.main import app
        db.configure(Path(temp) / "panel.db")
        db.run_migrations()
        fixture = json.loads(Path("tests/fixtures/animeunity_browse_2026_11_18.json").read_text())
        sample = fixture["archive_sample"]["data"][0]
        titles = [dict(copy.deepcopy(sample), id=n, slug=f"title-{n}", title=f"Anime {n}", type="movie") for n in range(1, 126)]
        videos = [{"id": n, "language": "ita", "episode": {"number": str(n), "anime": titles[n % 125]}} for n in range(1, 181)]
        calls = []
        def source(path, **params):
            calls.append((path, params))
            if path == "/api/calendar": return copy.deepcopy(fixture["calendar"])
            size = 20 if path.endswith("latest-episodes") else 30
            rows = videos if size == 20 else titles
            number = params.get("page", 1)
            return {"data": rows[(number - 1) * size:number * size], "current_page": number,
                    "per_page": size, "total": len(rows), "last_page": (len(rows) + size - 1) // size}
        au._modern_site = lambda: True
        home_fixture = json.loads(Path("tests/fixtures/animeunity_home_2026_11_18.json").read_text())
        au.fetch_home_html = lambda: '<div id="app" data-page="' + html.escape(json.dumps({"props": home_fixture["props"]}), quote=True) + '"></div>'
        au.get_episodes = lambda *a, **kw: []
        catalog._json = source
        artifacts = Path("tmp/animeunity-browser")
        artifacts.mkdir(parents=True, exist_ok=True)
        errors = []
        client = TestClient(app)  # Deliberately do not enter the lifespan.
        def handle(route):
            request = route.request
            parsed = urlsplit(request.url)
            if parsed.hostname != "127.0.0.1":
                if parsed.hostname == "img.animeunity.cx": return route.continue_()
                return route.abort()
            path = parsed.path
            if path == "/api/domain":
                return route.fulfill(json={"domain": "example.test", "valid": True})
            if path.startswith("/api/") and not path.startswith(("/api/anime/", "/api/auth/", "/api/requests/status", "/api/home")):
                return route.fulfill(json={})
            response = client.request(request.method, path + ("?" + parsed.query if parsed.query else ""),
                                      content=request.post_data or None,
                                      headers={"host": "localhost", "content-type": request.headers.get("content-type", "")})
            route.fulfill(status=response.status_code, body=response.content,
                          content_type=response.headers.get("content-type", "application/octet-stream"))
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, channel=os.environ.get("PLAYWRIGHT_CHANNEL", "msedge"))
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/*", handle)
                page.goto("http://127.0.0.1:18765/#/search?src=animeunity", wait_until="domcontentloaded")
                expect(page.locator("#search-results .result-card")).to_have_count(60)
                expect(page.locator("#search-filters button")).to_have_count(7)
                for button in page.locator("#search-filters button").all(): expect(button).to_be_visible()
                page.locator('#anime-pagination-top [data-page="2"]').click()
                expect(page.locator('#anime-pagination-top input')).to_have_value('2')
                expect(page.locator('#search-results .card-title-text').first).to_have_text('Anime 62')
                page.locator('[data-action="anime:view"][data-view="home"]').click()
                expect(page.locator('#home-shelves .shelf')).to_have_count(7)
                expect(page.locator('#home-shelves [data-action="anime:view"]')).to_have_count(6)
                page.locator('#home-shelves [data-view="top_week"]').click()
                expect(page.locator('#anime-toolbar')).to_contain_text('Più visti della settimana')
                expect(page.locator('#search-results .result-card')).to_have_count(60)
                page.locator('[data-action="anime:view"][data-view="archive"]').click()
                expect(page.locator('#anime-toolbar')).to_contain_text('Archivio AnimeUnity')
                page.locator('[data-change="anime:size"]').select_option('30')
                expect(page.locator('#search-results .result-card')).to_have_count(30)
                page.locator('#anime-pagination-top [data-page="2"]').click()
                expect(page.locator('#search-results .card-title-text').first).to_have_text('Anime 31')
                page.locator('#search-results .result-card').first.click()
                expect(page.locator('[data-action="tp:crumb-search"]')).to_be_visible()
                page.locator('[data-action="tp:crumb-search"]').click()
                expect(page.locator('#anime-pagination-top input')).to_have_value('2')
                expect(page.locator('#search-results .card-title-text').first).to_have_text('Anime 31')
                page.locator('[data-action="search:kind"][data-kind="movie"]').click()
                expect(page.locator('#anime-pagination-top input')).to_have_value('1')
                page.locator('[data-action="search:dub"]').click()
                expect(page.locator('[data-action="search:dub"]')).to_have_attribute('aria-pressed', 'true')
                page.screenshot(path=str(artifacts / 'archive-it.png'))
                page.locator('[data-action="anime:view"][data-view="calendar"]').click()
                expect(page.locator('#anime-calendar-days button')).to_have_count(8)
                page.locator('#anime-calendar-days [data-day="1"]').click()
                expect(page.locator('#anime-calendar-days [data-day="1"]')).to_have_attribute('aria-pressed', 'true')
                page.evaluate("scLanguage.apply('en')")
                expect(page.locator('#anime-toolbar')).to_contain_text('Schedule / Calendar')
                expect(page.locator('#anime-calendar-days')).to_contain_text('Thursday')
                page.screenshot(path=str(artifacts / 'calendar-en.png'))
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator('[data-action="anime:view"][data-view="archive"]').click()
                expect(page.locator('#search-results .result-card')).to_have_count(30)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'horizontal page overflow'
                page.screenshot(path=str(artifacts / 'archive-mobile.png'))
                assert not errors, errors
                browser.close()
            print(json.dumps({"browser": "Chromium", "checks": "pagination, filters, archive, title return, calendar, English, mobile", "errors": errors, "source_calls": len(calls)}))
        finally:
            client.close()
            db.close_all()
            gc.collect()  # Release SQLite connections of completed TestClient worker threads.


if __name__ == "__main__":
    main()
