"""Bulk route planning must scale, avoid guesses, and stay tenant-isolated."""
import importlib
import re
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from bulk_dispatch import BulkPreviewRequest, generate_preview, sample_tasks
from dispatch import TaskInput


def demo_request(max_stops=2):
    return {"tasks": sample_tasks(), "max_stops_per_route": max_stops,
            "include_training": True, "include_demo": True}


@pytest.fixture
def auth(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DCC_ENV", "development")
    monkeypatch.setenv("DCC_OWNER_EMAIL", "bulk-owner@example.test")
    monkeypatch.setenv("DCC_ORGANIZATION_SLUG", "bulk-company")
    monkeypatch.setenv("DCC_DATABASE_URL", f"sqlite:///{tmp_path}/bulk.sqlite3")
    monkeypatch.delenv("DCC_SMTP_HOST", raising=False)
    monkeypatch.delenv("HEIGIT_API_KEY", raising=False)
    monkeypatch.delenv("DCC_OSRM_BASE_URL", raising=False)
    monkeypatch.delenv("DCC_ALLOW_PUBLIC_OSRM", raising=False)
    if "app" in sys.modules:
        module = importlib.reload(sys.modules["app"])
    else:
        module = importlib.import_module("app")
    with TestClient(module.app) as client:
        login(client, "bulk-owner@example.test", capsys)
        yield client, module


def login(client, email, capsys):
    result = client.post("/api/auth/request-link", json={"email": email})
    assert result.status_code == 200
    match = re.search(r"login_token=([A-Za-z0-9_-]+)", capsys.readouterr().out)
    assert match
    assert client.post("/api/auth/redeem", json={"token": match.group(1)}).status_code == 200


def test_three_hundred_fictional_tasks_cover_every_state():
    rows = sample_tasks()
    assert len(rows) == 300
    assert len({t["external_task_id"] for t in rows}) == 300
    assert len({t["external_task_id"].split("-")[2] for t in rows}) == 50
    assert all("DO NOT DISPATCH" in t["address"] for t in rows)
    assert all(t["task_type"] == "Demo service" for t in rows)


def test_bulk_preview_100_plus_routes_without_routing_key(auth):
    client, _ = auth
    before = client.post("/api/dispatch/bulk/preview", json=demo_request())
    assert before.status_code == 200, before.text
    assert before.json()["proposed_routes"] == 0
    assert before.json()["unmatched_tasks"] == 300
    assert client.post("/api/contractors/demo-seed").json()["created"] == 100

    result = client.post("/api/dispatch/bulk/preview", json=demo_request())
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["input_tasks"] == 300
    assert data["matched_tasks"] == 300
    assert data["unmatched_tasks"] == data["reserved_tasks"] == 0
    assert data["proposed_routes"] >= 100
    assert all(row["stop_count"] <= 2 for row in data["routes"])
    assert sum(row["task_count"] for row in data["routes"]) == 300
    assert all(row["is_demo"] for row in data["routes"])
    assert all(row["road_miles"] is None and row["price"] is None
               and row["status"] == "preview_only" for row in data["routes"])
    assert data["offers_sent"] == 0
    assert client.get("/api/routes").json() == []
    assert client.get("/api/routing/status").json()["ready"] is False

    again = client.post("/api/dispatch/bulk/preview", json=demo_request())
    assert again.status_code == 200
    assert [r["draft_id"] for r in data["routes"]] == [
        r["draft_id"] for r in again.json()["routes"]]


def test_physical_stop_is_never_split():
    tasks = [
        TaskInput(external_task_id=f"TSK-{i}", venue="Sample", address="Fictional Site",
                  lat=41.881, lon=-87.629, task_type="Service")
        for i in (1, 2, 3)
    ]
    candidate = dict(external_id="c-1", name="Worker", status="active",
                     home={"lat": 41.88, "lon": -87.63}, is_demo=False,
                     service_radius_miles=20)
    result = generate_preview(tasks, [candidate], set(), 1)
    assert result["proposed_routes"] == 1
    assert result["physical_stops"] == 1
    assert result["routes"][0]["task_count"] == 3


def test_duplicate_id_is_rejected_before_any_planning(auth):
    client, _ = auth
    record = sample_tasks()[0]
    dup = dict(record, external_task_id=record["external_task_id"].lower())
    response = client.post("/api/dispatch/bulk/preview",
                           json={"tasks": [record, dup], "include_demo": True})
    assert response.status_code == 422
    assert "Duplicate" in response.json()["detail"]


def test_reserved_tasks_skipped_without_creating_duplicates(auth, monkeypatch):
    client, _ = auth
    assert client.post("/api/contractors/demo-seed").status_code == 200
    sample = sample_tasks()[0]
    import dispatch
    monkeypatch.setattr(dispatch, "roundtrip", lambda home, stops: {
        "road_miles": 4.2, "driving_hours": .22, "stop_order": [0],
        "provider": "test-mock", "optimization": "test-mock"})
    create = client.post("/api/dispatch/routes", json={
        "title": "Simulated existing route", "contractor": "Demo",
        "home": {"lat": sample["lat"], "lon": sample["lon"]},
        "tasks": [sample],
        "pricing": {"base_fee": 0, "per_stop": 20, "per_mile": 0,
                    "per_hour": 0, "minimum": 0},
    })
    assert create.status_code == 201, create.text
    response = client.post("/api/dispatch/bulk/preview", json=demo_request(2))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["reserved_tasks"] == 1
    assert data["matched_tasks"] == 299
    assert data["reserved"][0]["external_task_id"] == sample["external_task_id"]
    assert data["proposed_routes"] >= 100


def test_tenant_a_cannot_see_b_contractors_or_reserved_work(auth, capsys):
    a, module = auth
    with TestClient(module.app) as b:
        with module.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO dcc_staff_identities (email) VALUES ('bulk-b@example.test')"))
            connection.execute(text("""
                INSERT INTO dcc_staff_memberships (identity_id, organization, role)
                SELECT id, 'company-b', 'owner' FROM dcc_staff_identities
                WHERE email = 'bulk-b@example.test'
            """))
        login(b, "bulk-b@example.test", capsys)
        assert a.post("/api/contractors/demo-seed").json()["created"] == 100
        preview_a = a.post("/api/dispatch/bulk/preview",
                           json={"tasks": sample_tasks()[:5], "include_demo": True})
        preview_b = b.post("/api/dispatch/bulk/preview",
                           json={"tasks": sample_tasks()[:5], "include_demo": True})
        assert preview_a.json()["matched_tasks"] == 5
        assert preview_b.json()["matched_tasks"] == 0
        assert preview_b.json()["unmatched_tasks"] == 5


def test_unmatched_is_explicit_and_road_mileage_never_fabricated():
    tasks = [TaskInput(external_task_id="far-away", venue="Example",
                       address="Fictional remote address", lat=61.2, lon=-149.9)]
    candidates = [dict(external_id="chicago", name="Worker",
                       status="active", service_radius_miles=10,
                       home={"lat": 41.88, "lon": -87.63})]
    result = generate_preview(tasks, candidates, set(), 6)
    assert result["matched_tasks"] == 0
    assert result["unmatched_tasks"] == 1
    assert result["routes"] == []
    assert "No active contractor" in result["unmatched"][0]["reason"]
