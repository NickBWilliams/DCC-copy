"""API integration tests. No employer systems, email accounts, or network required."""
import os
import re
import tempfile
from urllib.parse import urlparse, parse_qs

import pytest

fd, dbpath = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["DCC_DATABASE_URL"] = "sqlite:///" + dbpath
os.environ["DCC_ENV"] = "development"
os.environ["DCC_OWNER_EMAIL"] = "nicholas.b.williams@outlook.com"
os.environ["DCC_ORGANIZATION_SLUG"] = "personal"

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app import app, engine, Route

@pytest.fixture
def client(capsys):
    with TestClient(app) as test_client:
        r = test_client.post("/api/auth/request-link",
            json={"email": os.environ["DCC_OWNER_EMAIL"]})
        assert r.status_code == 200, r.text
        output = capsys.readouterr().out
        # The authentication service rate-limits links to once per minute,
        # so tests create a new owner sign-in link with a fresh database entry.
        if "login_token=" not in output:
            from sqlalchemy import delete
            from app import Base
            with engine.begin() as conn:
                conn.execute(delete(Base.metadata.tables["dcc_staff_login_links"]))
            r = test_client.post("/api/auth/request-link",
                json={"email": os.environ["DCC_OWNER_EMAIL"]})
            assert r.status_code == 200, r.text
            output = capsys.readouterr().out
        tokens = re.findall(r"login_token=([a-zA-Z0-9_-]+)", output)
        assert tokens, output
        login = test_client.post("/api/auth/redeem", json={"token": tokens[-1]})
        assert login.status_code == 200, login.text
        assert login.json()["email"] == os.environ["DCC_OWNER_EMAIL"]
        assert login.json()["role"] == "owner"
        yield test_client

def create_route(client, title="Chicago", **extra):
    payload = {"title": title, "contractor": "Alex", "stops": 3, "tasks": 4, "price": 100}
    payload.update(extra)
    response = client.post("/api/routes", json=payload)
    assert response.status_code == 201, response.text
    return response.json()

def transition(client, route, action, amount=None):
    result = client.post(f"/api/routes/{route['id']}/transition",
        json={"action": action, "amount": amount, "expected_version": route["version"]})
    assert result.status_code == 200, result.text
    return result.json()

def offer_and_respond(client, route, action, amount=None):
    issued = client.post(f"/api/routes/{route['id']}/offers")
    assert issued.status_code == 201, issued.text
    token = issued.json()["token"]
    assert len(token) > 30
    response = client.post("/api/offers/"+token+"/respond",
                           json={"action": action, "amount": amount})
    assert response.status_code == 200, response.text
    return response.json(), token

def test_login_and_tenant_restrictions(client):
    assert client.get("/api/auth/me").json()["organization"] == "personal"
    route = create_route(client)
    with Session(engine) as db:
        other = Route(organization="other-company", title="Private", stops=1, tasks=1, price=25)
        db.add(other)
        db.commit()
        other_id = other.id
    assert all(r["organization"] == "personal" for r in client.get("/api/routes").json())
    assert client.get(f"/api/routes/{other_id}/events").status_code == 404
    assert client.post(f"/api/routes/{other_id}/offers").status_code == 404
    assert client.post(f"/api/routes/{other_id}/transition",
        json={"action": "prepare", "expected_version": 1}).status_code == 404
    assert client.get("/api/routes", headers={"X-Organization": "other-company"}).json()[0]["id"] == route["id"]
    anonymous = TestClient(app)
    assert anonymous.get("/api/routes").status_code == 401

def test_route_offer_lifecycle(client):
    route = create_route(client)
    route = transition(client, route, "prepare")
    answer, old_token = offer_and_respond(client, route, "counter", 150)
    assert answer["status"] == "countered"
    assert client.get("/api/offers/"+old_token).status_code == 410
    route = client.get("/api/routes").json()[0]
    assert route["proposed_price"] == "150.00"
    route = transition(client, route, "approve_counter")
    assert route["price"] == "150.00"
    answer, token = offer_and_respond(client, route, "accept")
    assert answer["status"] == "accepted"
    route = client.get("/api/routes").json()[0]
    route = transition(client, route, "finalize")
    route = transition(client, route, "complete")
    assert route["done"] == 4
    assert route["status"] == "completed"
    assert client.get(f"/api/routes/{route['id']}/events").status_code == 200

def test_logout_revokes_session(client):
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401
