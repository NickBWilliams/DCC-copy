import os
import tempfile
from pathlib import Path

fd, dbpath = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["DCC_DATABASE_URL"] = "sqlite:///" + dbpath
os.environ["DCC_ADMIN_KEY"] = "test-only-secret"
from fastapi.testclient import TestClient
from app import app, Base, engine

Base.metadata.create_all(engine)
client = TestClient(app)
HEADERS = {"X-Organization": "demo", "X-API-Key": "test-only-secret"}

def test_route_lifecycle_and_tenant_isolation():
    response = client.post("/api/routes", headers=HEADERS, json={"title": "Chicago", "contractor": "Alex", "stops": 3, "tasks": 4, "price": 100})
    assert response.status_code == 201, response.text
    route = response.json()
    for action, amount, expected in [
        ("prepare", None, "ready"), ("send", None, "sent"),
        ("counter", 150, "countered"), ("approve_counter", None, "sent"),
        ("accept", None, "accepted"), ("finalize", None, "finalized"),
        ("complete", None, "completed")
    ]:
        response = client.post(f"/api/routes/{route['id']}/transition", headers=HEADERS, json={"action": action, "amount": amount, "expected_version": route["version"]})
        assert response.status_code == 200, response.text
        route = response.json()
        assert route["status"] == expected
    assert route["price"] == "150.00"
    assert route["done"] == 4
    assert client.get("/api/routes", headers={**HEADERS, "X-Organization": "other"}).json() == []
    assert client.get("/api/routes", headers={**HEADERS, "X-API-Key": "wrong"}).status_code == 401
    assert client.post(f"/api/routes/{route['id']}/transition", headers=HEADERS, json={"action": "requeue", "expected_version": 1}).status_code == 409
    assert len(client.get(f"/api/routes/{route['id']}/events", headers=HEADERS).json()) == 8
