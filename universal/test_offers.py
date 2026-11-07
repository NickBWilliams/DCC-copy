"""Contractor links are one-time, revocable, company-scoped and login-free."""
from test_app import client, create_route, transition, offer_and_respond

def test_passwordless_counter_and_replay_protection(client):
    route = transition(client, create_route(client, "Offer test", stops=2, tasks=2, price=50), "prepare")
    issued = client.post(f"/api/routes/{route['id']}/offers")
    assert issued.status_code == 201, issued.text
    token = issued.json()["token"]
    assert client.get("/api/offers/"+token).json()["price"] == "50.00"
    response = client.post("/api/offers/"+token+"/respond", json={"action":"counter","amount":75})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "countered"
    assert client.post("/api/offers/"+token+"/respond",json={"action":"accept"}).status_code == 410

def test_revoke_prevents_old_link(client):
    route = transition(client, create_route(client,"Revoke test",stops=1,tasks=1,price=25), "prepare")
    issued = client.post(f"/api/routes/{route['id']}/offers").json()
    assert client.post(f"/api/routes/{route['id']}/offers/revoke").status_code == 200
    assert client.get("/api/offers/"+issued["token"]).status_code == 410
    assert client.post("/api/offers/"+issued["token"]+"/respond",json={"action":"accept"}).status_code == 410

def test_new_offer_revokes_previous_link(client):
    route = transition(client, create_route(client,"Duplicate test"), "prepare")
    first = client.post(f"/api/routes/{route['id']}/offers").json()["token"]
    second = client.post(f"/api/routes/{route['id']}/offers").json()["token"]
    assert first != second
    assert client.get("/api/offers/"+first).status_code == 410
    assert client.get("/api/offers/"+second).status_code == 200
    assert client.post("/api/offers/"+second+"/respond",json={"action":"decline"}).status_code == 200
    assert client.post("/api/offers/"+second+"/respond",json={"action":"decline"}).status_code == 410

def test_offer_requires_staff_to_issue(client):
    route = transition(client, create_route(client,"Protected offer test"), "prepare")
    from fastapi.testclient import TestClient
    from app import app
    anonymous = TestClient(app)
    assert anonymous.post(f"/api/routes/{route['id']}/offers").status_code == 401
