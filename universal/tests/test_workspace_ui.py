"""Verify the sellable workspace layout ships with real functioning assets."""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.nav_views = []
        self.scripts = []
        self.styles = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if "data-view" in attributes:
            self.nav_views.append(attributes["data-view"])
        if tag == "script" and attributes.get("src"):
            self.scripts.append(attributes["src"])
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.styles.append(attributes.get("href"))


def test_workspaces_keep_every_functional_form_and_new_sections():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    parser = Elements()
    parser.feed(html)
    duplicated = [item for item, count in Counter(parser.ids).items() if count > 1]
    assert duplicated == [], f"Duplicate controls: {duplicated}"
    assert {"dashboard","routes","contractors","integrations",
            "analytics","settings"}.issubset(set(parser.nav_views))
    assert "/assets/workspace.css" in parser.styles
    assert "/assets/workspace.js" in parser.scripts
    assert {"metrics","routes","details","itinerary","search","routeSort",
            "rosterPanel","builder","integrationsPanel","analyticsPanel",
            "settingsPanel","contractors","integrations","seedDemo",
            "clearDemo","importFile","applyImport","previewImport",
            "integrationSave","integrationSync","integrationPreview",
            "previewPlan","savePlan","matchContractors","refresh",
            "signOut","identity","company","notice","events"}.issubset(set(parser.ids))
    # A sellable product cannot render sample route data as if it came from a client.
    assert "Acme Services" not in html
    assert "Tammy Costa" not in html
    assert "River Valley Route" not in html


def test_fastapi_serves_actual_redesign_assets_without_public_login():
    from app import app
    client = TestClient(app)
    main = client.get("/")
    assert main.status_code == 200
    assert 'src="/assets/workspace.js"' in main.text
    css = client.get("/assets/workspace.css")
    js = client.get("/assets/workspace.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert "DCC Universal" in css.text
    assert "window.dccUI" in js.text
    assert "routes.filter" in js.text
    assert "Acme Services" not in js.text
