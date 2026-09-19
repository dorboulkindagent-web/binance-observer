"""Browser checks use synthetic candles, never fabricated live-market evidence."""
import copy
import json
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from playwright.sync_api import sync_playwright

now = int(time.time() * 1000)

def fixture(symbol, interval):
    return [[now-(60-i)*60000, "100", "101", "99", str(100+i), "2",
             now-(59-i)*60000-1] for i in range(60)]

observer = app.Observer(":memory:", fetcher=fixture)
observer.refresh(now)
server = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(observer))
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
checks = []
output = Path(os.getenv("BROWSER_ARTIFACTS", "artifacts"))
output.mkdir(parents=True, exist_ok=True)
try:
    with sync_playwright() as p:
        options = {"headless": True, "args": ["--no-sandbox"]}
        if os.getenv("CHROMIUM_PATH"):
            options["executable_path"] = os.environ["CHROMIUM_PATH"]
        browser = p.chromium.launch(**options)
        page = browser.new_page(viewport={"width": 1280, "height": 960})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        response = page.goto(f"http://127.0.0.1:{server.server_port}/")
        assert response.status == 200
        page.wait_for_function("document.getElementById('status').textContent === 'התקבלו נתונים תקינים'")
        assert page.locator("#cards article").count() == 2
        checks.append("desktop dashboard and two market cards render")
        page.get_by_role("button", name="רענון").click()
        page.wait_for_function("!document.getElementById('refresh').disabled")
        checks.append("refresh button completes")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        checks.append("mobile layout has no horizontal overflow")
        page.evaluate("document.querySelector('.top').appendChild(Object.assign(document.createElement('p'),{textContent:'בדיקת ממשק — נתונים מדומים'}))")
        page.screenshot(path=str(output / "mobile-fixture.png"), full_page=True)
        snapshot = observer.read()
        for value in snapshot["symbols"].values():
            value["stale"] = True
        page.route("**/data.json?*", lambda route: route.fulfill(json=snapshot))
        page.get_by_role("button", name="רענון").click()
        page.wait_for_function("document.getElementById('status').textContent === 'הנתונים אינם עדכניים'")
        assert page.locator("article[data-stale=true]").count() == 2
        checks.append("stale observations are visibly marked")
        page.unroute("**/data.json?*")
        page.route("**/data.json?*", lambda route: route.abort())
        page.get_by_role("button", name="רענון").click()
        page.wait_for_function("document.getElementById('status').textContent === 'לא ניתן לעדכן את התצוגה'")
        assert "159.00" in page.locator("#cards").inner_text()
        checks.append("network failure preserves previous observations with an error")
        page.unroute("**/data.json?*")
        page.route("**/data.json?*", lambda route: route.fulfill(json={"updated_at": None, "symbols": {}}))
        page.get_by_role("button", name="רענון").click()
        page.wait_for_function("document.getElementById('status').textContent === 'ממתין לאיסוף הראשון'")
        assert "Invalid Date" not in page.locator("body").inner_text()
        checks.append("first-run empty state never displays Invalid Date")
        page.unroute("**/data.json?*")
        page.route("**/data.json?*", lambda route: route.fulfill(body="not json", content_type="application/json"))
        page.get_by_role("button", name="רענון").click()
        page.wait_for_function("document.getElementById('status').textContent === 'לא ניתן לעדכן את התצוגה'")
        checks.append("invalid server JSON is handled")
        assert not errors, errors
        checks.append("no uncaught JavaScript errors")
        browser.close()
finally:
    server.shutdown()
    server.server_close()
    thread.join()
    observer.close()
report = {"passed": len(checks), "checks": checks, "data": "synthetic fixtures, not live market data"}
(output / "browser-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(report, ensure_ascii=False))
