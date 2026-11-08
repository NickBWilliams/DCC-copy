"""Contractor demo roster, source-aware imports and source adapter safety tests."""
import importlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

from contractor_sources import _trusted_url, source_key, translate_records
from demo_contractors import CITY_ANCHORS, demo_rows


def test_100_fictional_contractors_span_all_fifty_states():
    rows = demo_rows()
    assert len(rows) == 100
    assert len(CITY_ANCHORS) == 50
    assert len({r["external_id"] for r in rows}) == 100
    assert len({r["external_id"].split("-")[1] for r in rows}) == 50
    assert all(r["name"].startswith("Demo Installer") for r in rows)
    assert all("FICTIONAL / DO NOT SHIP" in r["home_address"] for r in rows)
    assert all(r["email"] is None for r in rows)


@pytest.fixture
def authorized_client(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DCC_ENV", "development")
    monkeypatch.setenv("DCC_OWNER_EMAIL", "demo-owner@example.test")
    monkeypatch.setenv("DCC_ORGANIZATION_SLUG", "personal")
    monkeypatch.setenv("DCC_DATABASE_URL", f"sqlite:///{tmp_path}/roster.sqlite3")
    monkeypatch.delenv("DCC_SMTP_HOST", raising=False)
    # Reload after setting env: app creates its SQLAlchemy engine at import.
    if "app" in sys.modules:
        module = importlib.reload(sys.modules["app"])
    else:
        module = importlib.import_module("app")
    with TestClient(module.app) as client:
        response = client.post("/api/auth/request-link", json={"email": "demo-owner@example.test"})
        assert response.status_code == 200
        stdout = capsys.readouterr().out
        match = re.search(r"login_token=([A-Za-z0-9_-]+)", stdout)
        assert match, "Development login URL should be printed to server logs"
        signed = client.post("/api/auth/redeem", json={"token": match.group(1)})
        assert signed.status_code == 200
        yield client


def example_record(external="123", name="Imported Demo"):
    return {
        "external_id": external,
        "name": name,
        "email": None,
        "status": "active",
        "home": {"lat": 41.8781, "lon": -87.6298},
        "home_address": "Fictional Example Way, Chicago, IL",
        "service_radius_miles": 85,
    }


def test_demo_seed_is_idempotent_and_discoverable(authorized_client):
    client = authorized_client
    first = client.post("/api/contractors/demo-seed")
    assert first.status_code == 200, first.text
    assert first.json()["created"] == 100
    repeat = client.post("/api/contractors/demo-seed")
    assert repeat.status_code == 200
    assert repeat.json()["created"] == 0
    assert repeat.json()["already_present"] == 100
    roster = client.get("/api/contractors").json()
    assert len(roster) == 100
    assert all(x["is_demo"] for x in roster)
    assert {x["source"] for x in roster} == {"demo"}
    assert all("DO NOT SHIP" in x["home_address"] for x in roster)
    sources = client.get("/api/contractors/sources").json()
    assert sources["sources"][0]["count"] == 100
    nearby = client.get("/api/contractors/nearby?lat=41.8781&lon=-87.6298&include_training=true")
    assert nearby.status_code == 200
    assert nearby.json()["candidates"]
    assert all(c["is_demo"] for c in nearby.json()["candidates"])
    cleared = client.post("/api/contractors/demo-clear")
    assert cleared.status_code == 200 and cleared.json()["removed"] == 100
    assert client.get("/api/contractors").json() == []


def test_csv_api_batch_preview_sync_idempotent_multi_source(authorized_client):
    client = authorized_client
    data = {"source": "company-a", "contractors": [example_record()]}
    preview = client.post("/api/contractors/import/preview", json=data)
    assert preview.status_code == 200, preview.text
    assert (preview.json()["create"], preview.json()["applied"]) == (1, False)
    assert client.get("/api/contractors").json() == []
    created = client.post("/api/contractors/import/apply", json=data)
    assert created.status_code == 200, created.text
    assert created.json()["create"] == 1
    again = client.post("/api/contractors/import/apply", json=data)
    assert again.status_code == 200 and again.json()["unchanged"] == 1
    data["contractors"][0]["name"] = "Updated Installer"
    updated = client.post("/api/contractors/import/apply", json=data)
    assert updated.status_code == 200 and updated.json()["update"] == 1
    second_source = {"source": "company-b", "contractors": [example_record()]}
    assert client.post("/api/contractors/import/apply", json=second_source).status_code == 200
    roster = client.get("/api/contractors").json()
    assert len(roster) == 2
    assert {x["external_id"] for x in roster} == {"company-a:123", "company-b:123"}
    assert {x["source"] for x in roster} == {"company-a", "company-b"}
    assert next(x for x in roster if x["source"] == "company-a")["version"] == 2


def test_import_rejects_duplicates_and_never_adopts_manual_entries(authorized_client):
    client = authorized_client
    bad = {"source": "company-a", "contractors": [example_record(), example_record()]}
    assert client.post("/api/contractors/import/apply", json=bad).status_code == 422
    manual = example_record("company-a:123")
    manual.pop("home_address")
    assert client.post("/api/contractors", json=manual).status_code == 201
    single = {"source": "company-a", "contractors": [example_record()]}
    assert client.post("/api/contractors/import/apply", json=single).status_code == 409
    assert client.get("/api/contractors").json()[0]["source"] == "manual"


def test_connector_host_restrictions_and_mappings():
    assert _trusted_url("https://api.client.example.com/contractors")[1] == "api.client.example.com"
    for url in ["http://api.example.com", "https://localhost/secret",
                "https://127.0.0.1/secret",
                "https://10.0.0.1/data",
                "https://api.example.com:444/data",
                "https://user:password@api.example.com/"]:
        with pytest.raises(ValueError):
            _trusted_url(url)
    assert source_key("company-a") == "company-a"
    with pytest.raises(ValueError):
        source_key("demo")
    cfg = {
        "records_path": "results",
        "mapping": {"external_id": "worker.id", "name": "worker.name",
                    "latitude": "location.lat", "longitude": "location.lon",
                    "home_address": "location.address", "status": "state"},
        "status_map": {"approved": "active"},
    }
    converted = translate_records({"results": [{
        "worker": {"id": "ABC-12", "name": "Approved Contractor"},
        "location": {"lat": 44.9778, "lon": -93.265, "address": "Sample location"},
        "state": "approved"
    }]}, cfg)
    assert converted[0]["status"] == "active"
    assert converted[0]["external_id"] == "ABC-12"
    assert converted[0]["home_address"] == "Sample location"
