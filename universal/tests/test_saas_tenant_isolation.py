"""Two fictional customers share an app, not each other's data."""
import importlib
import re
import sys

from fastapi.testclient import TestClient
from sqlalchemy import text


def _login(client, email, capsys):
    response = client.post("/api/auth/request-link", json={"email": email})
    assert response.status_code == 200, response.text
    output = capsys.readouterr().out
    match = re.search(r"login_token=([A-Za-z0-9_-]+)", output)
    assert match, "Development login link should be printed on the private server"
    verified = client.post("/api/auth/redeem", json={"token": match.group(1)})
    assert verified.status_code == 200, verified.text


def _contractor(name):
    return {"external_id": "same-external-id", "name": name,
            "email": None, "status": "active",
            "home": {"lat": 40.72, "lon": -74.0},
            "service_radius_miles": 75,
            "home_address": "Fictional Test Address"}


def test_two_customers_have_independent_rosters_and_routes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DCC_ENV", "development")
    monkeypatch.setenv("DCC_OWNER_EMAIL", "first-owner@example.test")
    monkeypatch.setenv("DCC_ORGANIZATION_SLUG", "company-a")
    monkeypatch.setenv("DCC_DATABASE_URL", f"sqlite:///{tmp_path}/tenants.sqlite3")
    monkeypatch.delenv("DCC_SMTP_HOST", raising=False)
    monkeypatch.delenv("DCC_CONTRACTOR_CONNECTORS_JSON", raising=False)
    if "app" in sys.modules:
        module = importlib.reload(sys.modules["app"])
    else:
        module = importlib.import_module("app")

    with TestClient(module.app) as a, TestClient(module.app) as b:
        # Second fictional tenant is provisioned server-side, never
        # implicitly from a request's user-supplied organization name.
        with module.engine.begin() as connection:
            connection.execute(
                text("INSERT INTO dcc_staff_identities (email) VALUES (:email)"),
                {"email": "second-owner@example.test"},
            )
            connection.execute(text("""
                INSERT INTO dcc_staff_memberships (identity_id, organization, role)
                SELECT id, 'company-b', 'owner' FROM dcc_staff_identities
                WHERE email = :email
            """), {"email": "second-owner@example.test"})

        _login(a, "first-owner@example.test", capsys)
        _login(b, "second-owner@example.test", capsys)
        assert a.get("/api/auth/me").json()["organization"] == "company-a"
        assert b.get("/api/auth/me").json()["organization"] == "company-b"

        # An identity may not switch to a tenant in which it lacks membership.
        blocked = a.post("/api/auth/switch", json={"organization": "company-b"})
        assert blocked.status_code == 403
        assert a.get("/api/auth/me").json()["organization"] == "company-a"

        # Same source ID is safe because each organization owns its own copy.
        body_a = {"source": "company-roster", "contractors": [_contractor("Alpha Installer")]}
        body_b = {"source": "company-roster", "contractors": [_contractor("Bravo Installer")]}
        assert a.post("/api/contractors/import/apply", json=body_a).status_code == 200
        assert b.post("/api/contractors/import/apply", json=body_b).status_code == 200
        roster_a = a.get("/api/contractors").json()
        roster_b = b.get("/api/contractors").json()
        assert len(roster_a) == len(roster_b) == 1
        assert roster_a[0]["name"] == "Alpha Installer"
        assert roster_b[0]["name"] == "Bravo Installer"
        assert roster_a[0]["external_id"] == roster_b[0]["external_id"]
        assert a.get("/api/contractors/nearby?lat=40.72&lon=-74").json()["candidates"][0]["name"] == "Alpha Installer"
        assert b.get("/api/contractors/nearby?lat=40.72&lon=-74").json()["candidates"][0]["name"] == "Bravo Installer"

        # Updating company A's existing source ID must not change company B.
        body_a["contractors"][0]["name"] = "Alpha Updated"
        assert a.post("/api/contractors/import/apply", json=body_a).json()["update"] == 1
        assert b.get("/api/contractors").json()[0]["name"] == "Bravo Installer"
        assert a.get("/api/contractors").json()[0]["name"] == "Alpha Updated"

        # Separate route lists and forbidden cross-company route access.
        route_a = a.post("/api/routes", json={
            "title": "Tenant A", "contractor": "Alpha Updated",
            "stops": 1, "tasks": 1, "price": "20.00",
        })
        assert route_a.status_code == 201, route_a.text
        route_id = route_a.json()["id"]
        assert len(a.get("/api/routes").json()) == 1
        assert b.get("/api/routes").json() == []
        assert b.get(f"/api/routes/{route_id}/events").status_code == 404
        assert b.post(f"/api/routes/{route_id}/transition", json={
            "action": "prepare", "expected_version": 1,
        }).status_code == 404
        assert a.get("/api/routes").json()[0]["status"] == "unassigned"

        # Tenant-scoped contractor source inventory must not expose A's data.
        assert b.get("/api/contractors/sources").json()["sources"][0]["count"] == 1
        assert a.get("/api/contractors/sources").json()["sources"][0]["count"] == 1
