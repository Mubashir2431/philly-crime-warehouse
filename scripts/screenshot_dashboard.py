"""Take full-page screenshots of the running dashboard for the README.

Start the app first:  streamlit run dashboard/app.py --server.port 8599
Then:                 python scripts/screenshot_dashboard.py [url]

Uses Playwright with the locally installed Chrome (channel="chrome"), so no
separate browser download is needed.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8599"
OUT = Path(__file__).resolve().parents[1] / "docs" / "img"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        # Streamlit scrolls inside its own container, so a tall viewport is the
        # simplest way to capture the whole page in one image.
        page = browser.new_page(viewport={"width": 1400, "height": 1900})
        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector("text=Hotspot map", timeout=60_000)
        page.wait_for_timeout(8000)  # let Plotly and the map tiles finish drawing
        page.screenshot(path=str(OUT / "dashboard_full.png"))
        page.screenshot(
            path=str(OUT / "dashboard_top.png"), clip={"x": 0, "y": 0, "width": 1400, "height": 950}
        )
        page.screenshot(
            path=str(OUT / "dashboard_heatmap_map.png"),
            clip={"x": 0, "y": 960, "width": 1400, "height": 450},
        )
        browser.close()
    print("screenshots ->", OUT)


if __name__ == "__main__":
    main()
