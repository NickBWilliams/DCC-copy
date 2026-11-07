"""Contractor offers are one-time, revocable, and organization scoped."""
from test_app import client, HEADERS

def test_passwordless_offer_and_replay_protection():
    route = client.post("/api/routes", headers=HEADERS, json={"title":"Offer test","stops":2,"tasks":2,"price":50}).json()
    route = client.post(f"/api/routes/{route['id']}/transition",headers=HEADERS,json={"action":"prepare","expected_version":route["version"]}).json()
    issue = client.post(f"/api/routes/{route['id']}/offers",headers=HEADERS).json()
    token = issue["token"]
    assert len(token) >= 30
    assert client.get("/api/offers/"+token).json()["price"] == "50.00"
    response = client.post("/api/offers/"+token+"/respond",json={"action":"counter","amount":75})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "countered"
    assert client.post("/api/offers/"+token+"/respond",json={"action":"accept"}).status_code == 410

def test_revoke_prevents_old_link():
    route = client.post("/api/routes", headers=HEADERS, json={"title":"Revoke test","stops":1,"tasks":1,"price":25}).json()
    route = client.post(f"/api/routes/{route['id']}/transition",headers=HEADERS,json={"action":"prepare","expected_version":route["version"]}).json()
    offer = client.post(f"/api/routes/{route['id']}/offers",headers=HEADERS).json()
    assert client.post(f"/api/routes/{route['id']}/offers/revoke",headers=HEADERS).status_code == 200
    assert client.get("/api/offers/"+offer["token"]).status_code == 410
    assert client.post("/api/offers/"+offer["token"]+"/respond",json={"action":"accept"}).status_code == 410
