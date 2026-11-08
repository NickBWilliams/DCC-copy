"""Contractor map invitations and HTML mail remain read-only until explicit send."""
import importlib
import re
import sys
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from offer_email import render_email, delivery_status


@pytest.fixture
def owner(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DCC_ENV", "development")
    monkeypatch.setenv("DCC_OWNER_EMAIL", "invitation-owner@example.test")
    monkeypatch.setenv("DCC_ORGANIZATION_SLUG", "test-workspace")
    monkeypatch.setenv("DCC_DATABASE_URL", f"sqlite:///{tmp_path}/offers.sqlite3")
    monkeypatch.setenv("DCC_PUBLIC_URL", "http://localhost:8000")
    monkeypatch.delenv("DCC_SMTP_HOST", raising=False)
    monkeypatch.delenv("DCC_SMTP_FROM", raising=False)
    if "app" in sys.modules:
        module = importlib.reload(sys.modules["app"])
    else:
        module = importlib.import_module("app")
    with TestClient(module.app) as client:
        assert client.post("/api/auth/request-link",
                           json={"email":"invitation-owner@example.test"}).status_code == 200
        match = re.search(r"login_token=([A-Za-z0-9_-]+)", capsys.readouterr().out)
        assert match
        assert client.post("/api/auth/redeem",
                           json={"token": match.group(1)}).status_code == 200
        yield client


def ready_route(client):
    response=client.post("/api/routes",json={
        "title":"FICTIONAL route preview", "contractor":"Test Installer",
        "stops":3,"tasks":3,"price":"125.00"
    })
    assert response.status_code == 201,response.text
    route=response.json()
    prepared=client.post(f"/api/routes/{route['id']}/transition",
                         json={"action":"prepare","expected_version":route["version"]})
    assert prepared.status_code == 200,prepared.text
    return route["id"]


def test_email_preview_does_not_issue_or_send(owner):
    client=owner
    route_id=ready_route(client)
    response=client.get(f"/api/routes/{route_id}/offers/email-preview")
    assert response.status_code == 200,response.text
    preview=response.json()
    assert "View route &amp; respond" in preview["html"]
    assert "FICTIONAL route preview" in preview["html"]
    assert "DESIGN PREVIEW ONLY" in preview["html"]
    assert preview["can_send"] is False
    assert preview["recipient"] is None
    assert "https://preview.invalid/offer/NOT-A-REAL-LINK" in preview["html"]
    assert client.get("/api/routes").json()[0]["status"] == "ready"
    # User's localhost-only preview cannot deliver functional contractor email.
    sending=client.post(f"/api/routes/{route_id}/offers/send-email")
    assert sending.status_code == 503
    assert client.get("/api/routes").json()[0]["status"] == "ready"


def test_manual_offer_includes_email_design_and_revocable_map_link(owner):
    client=owner
    route_id=ready_route(client)
    generated=client.post(f"/api/routes/{route_id}/offers")
    assert generated.status_code == 201,generated.text
    body=generated.json()
    token=body["token"]
    assert "View route &amp; respond" in body["email_preview"]["html"]
    assert token in body["email_preview"]["html"]
    assert body["delivery"] == "manual_only"
    page=client.get(body["offer_path"])
    assert page.status_code == 200
    assert '/assets/offer-map.js' in page.text
    assert 'id="mapSection"' in page.text
    assert 'name="referrer" content="origin"' in page.text
    assert page.headers["Referrer-Policy"] == "origin"
    tiles=client.get("/assets/offer-map.js")
    assert tiles.status_code == 200
    assert 'https://www.google.com/maps/dir/' in tiles.text
    assert client.get("/assets/offer.css").status_code == 200
    offer=client.get("/api/offers/"+token)
    assert offer.status_code == 200
    assert offer.json()["company_name"] == "DCC Universal"
    assert offer.json()["map_tiles"]["url"].startswith("https://tile.openstreetmap.org")
    revoked=client.post(f"/api/routes/{route_id}/offers/revoke")
    assert revoked.status_code == 200
    assert client.get("/api/offers/"+token).status_code == 410


def test_email_html_escapes_untrusted_route_title():
    fake=SimpleNamespace(title="<script>alert('bad')</script>",
                         price="20.00", stops=2,tasks=3)
    result=render_email(
        organization="example-client", route=fake,
        expiration=datetime.now(timezone.utc)+timedelta(hours=48),
        link="https://preview.invalid/offer/example",
        company_name="Example & Services", plan={"road_miles":12.5,"driving_hours":0.6},
        preview=True)
    assert "<script>alert" not in result["html"]
    assert "&lt;script&gt;" in result["html"]
    assert "Example &amp; Services" in result["html"]
    assert "12.5 road miles" in result["html"]
    assert "View route" in result["html"]
    assert "accept" in result["text"].lower()


def test_email_delivery_requires_https_even_if_smtp_set(monkeypatch):
    monkeypatch.setenv("DCC_PUBLIC_URL","http://localhost:8000")
    monkeypatch.setenv("DCC_SMTP_HOST","smtp.example.test")
    monkeypatch.setenv("DCC_SMTP_FROM","no-reply@example.test")
    assert delivery_status()[0] is False
