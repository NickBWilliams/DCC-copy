"""No-login preview must be inspectable, fictional and inaccessible in production."""
import importlib
import sys
from fastapi.testclient import TestClient


def test_local_development_demo_without_staff(tmp_path, monkeypatch):
    monkeypatch.setenv("DCC_ENV", "development")
    monkeypatch.setenv("DCC_PUBLIC_URL", "http://localhost:8000")
    monkeypatch.setenv("DCC_OWNER_EMAIL", "owner@example.test")
    monkeypatch.setenv("DCC_ORGANIZATION_SLUG", "private")
    monkeypatch.setenv("DCC_DATABASE_URL", f"sqlite:///{tmp_path}/private_preview.sqlite3")
    monkeypatch.delenv("DCC_SMTP_HOST", raising=False)
    module = (importlib.reload(sys.modules["app"]) if "app" in sys.modules
              else importlib.import_module("app"))
    local = {"host": "localhost:8000"}
    with TestClient(module.app) as client:
        home = client.get("/", headers=local)
        assert home.status_code == 200
        assert 'Preview contractor page' in home.text
        assert 'href="/demo/route-invitation"' in home.text
        # This is available before any staff login or secret invitation token.
        page = client.get("/demo/route-invitation", headers=local)
        assert page.status_code == 200
        assert 'id="routeTitle"' in page.text
        assert 'id="payLarge"' in page.text
        assert 'id="demoBanner"' in page.text
        assert 'src="/assets/offer-map.js"' in page.text
        assert page.headers["Cache-Control"] == "no-store"
        assert page.headers["Referrer-Policy"] == "origin"
        assert "noindex" in page.headers["X-Robots-Tag"]

        single = client.get("/api/demo/route-invitation", headers=local)
        assert single.status_code == 200
        assert single.headers["Cache-Control"] == "no-store"
        data = single.json()
        assert data["demo"] is True
        assert data["stops"] == 1
        assert data["tasks"] == 1
        assert data["contractor"] == "Demo Contractor"
        assert "Fictional" in data["itinerary"][0]["address"]
        assert data["road_miles"] == 12.4
        assert data["itinerary"][0]["tasks"][0]["external_task_id"].startswith("DEMO-ONLY")
        multi = client.get("/api/demo/route-invitation?scenario=multi", headers=local)
        assert multi.status_code == 200
        assert multi.json()["stops"] == 6
        assert len(multi.json()["itinerary"]) == 6
        assert client.get("/api/routes", headers=local).status_code == 401
        assert client.get("/api/integrations", headers=local).status_code == 401
        assert client.get("/api/contractors", headers=local).status_code == 401
        assert client.get("/api/routes", headers=local).json()["detail"] == "Staff sign-in required"
        # There is no endpoint that can write simulated demo decisions.
        assert client.post("/api/demo/route-invitation", headers=local).status_code == 405
        assert client.get("/api/routes", headers=local).json()["detail"] == "Staff sign-in required"

        external = {"host": "example.com"}
        assert client.get("/demo/route-invitation", headers=external).status_code == 404
        assert client.get("/api/demo/route-invitation", headers=external).status_code == 404
        assert "Preview contractor page" not in client.get("/", headers=external).text

        monkeypatch.setenv("DCC_PUBLIC_URL", "https://dcc.example.com")
        assert client.get("/demo/route-invitation", headers=local).status_code == 404
        monkeypatch.setenv("DCC_PUBLIC_URL", "http://localhost:8000")
        monkeypatch.setenv("DCC_ENV", "production")
        assert client.get("/demo/route-invitation", headers=local).status_code == 404
        assert client.get("/api/demo/route-invitation", headers=local).status_code == 404
        assert "Preview contractor page" not in client.get("/", headers=local).text


def test_mobile_and_desktop_offer_assets_have_real_actions():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    markup = (root / "offer.html").read_text()
    css = (root / "assets" / "offer.css").read_text()
    js = (root / "assets" / "offer.js").read_text()
    map_js = (root / "assets" / "offer-map.js").read_text()
    for name in ("offerView", "routeTitle", "dueSummary", "roadMiles", "payLarge",
                 "contractorName", "mapPreview", "routeSchematic", "routeMap",
                 "actionPicker", "accept", "counter", "decline", "acceptFlow",
                 "counterFlow", "declineFlow", "certified", "legalName", "receipt"):
        assert f'id="{name}"' in markup
    assert "@media(max-width:630px)" in css
    assert "@media(max-width:920px)" in css
    assert "demoMode" in js
    assert "Nothing was sent" in js
    assert "respond('accept')" in js
    assert "respond('counter')" in js
    assert "respond('decline')" in js
    assert "setContent('dueDate'" not in js  # Was a nonexistent element.
    assert "showSchematic" in map_js
    assert "https://www.google.com/maps/dir/" in map_js
