"""Planned dispatch route tests; provider calls are all mocked."""
from decimal import Decimal

import pytest

from test_app import client
import dispatch

def sample(title="Naperville loop", task_id="TASK-100"):
    return {
        "title":title,"contractor":"Taylor (Independent)",
        "home":{"lat":41.775,"lon":-88.15},
        "tasks":[
            {"external_task_id":task_id,"venue":"Market A",
             "address":"100 Sample St, Naperville, IL 60540","lat":41.78,"lon":-88.14,
             "task_type":"Install"},
            {"external_task_id":"TASK-101","venue":"Market A",
             "address":"100 Sample St, Naperville, IL 60540","lat":41.78,"lon":-88.14,
             "task_type":"Removal"},
            {"external_task_id":"TASK-102","venue":"Market B",
             "address":"200 Example Rd, Aurora, IL 60505","lat":41.76,"lon":-88.2,
             "task_type":"Service"},
        ],
        "pricing":{"base_fee":10,"per_stop":5,"per_mile":0.5,"per_hour":10,"minimum":20}
    }


@pytest.fixture(autouse=True)
def isolate_task_claims(client):
    """Other tests keep historical routes; isolate only active task reservations."""
    from sqlalchemy import delete
    from app import Base, engine
    with engine.begin() as conn:
        conn.execute(delete(Base.metadata.tables["dcc_dispatch_task_claims"]))
    yield

@pytest.fixture
def road(monkeypatch):
    calls=[]
    def provider(home,stops):
        calls.append((home,stops))
        assert home == (-88.15,41.775)
        assert len(stops)==2
        leg=50 / 0.000621371 / 3
        return {"durations":[[0,2400,2500],[2300,0,3300],[3000,2400,0]],
                "distances":[[0,leg,leg],[leg,0,leg],[leg,leg,0]],"provider":"mock-road"}
    monkeypatch.setattr(dispatch,"road_matrix",provider)
    return calls

def test_quote_groups_tasks_at_same_location_and_uses_road_miles(client,road):
    response=client.post("/api/dispatch/quote",json=sample())
    assert response.status_code==200, response.text
    q=response.json()
    assert q["stops"]==2
    assert q["tasks"]==3
    assert q["price"]=="65.00"
    assert q["itinerary"][0]["venue"]=="Market B"
    assert q["itinerary"][1]["venue"]=="Market A"
    assert len(q["itinerary"][1]["tasks"])==2
    assert q["provider"]=="mock-road"
    assert len(road)==1

def test_planned_route_claims_tasks_and_sends_itinerary_to_contractor(client,road):
    r=client.post("/api/dispatch/routes",json=sample())
    assert r.status_code==201, r.text
    info=r.json()
    route=info["route"]
    assert route["status"]=="ready"
    assert route["stops"]==2 and route["tasks"]==3
    assert route["price"]=="65.00"
    assert info["plan"]["itinerary"][0]["venue"]=="Market B"
    details=client.get("/api/dispatch/routes/"+str(route["id"]))
    assert details.status_code==200
    issue=client.post(f"/api/routes/{route['id']}/offers")
    assert issue.status_code==201,issue.text
    token=issue.json()["token"]
    external=client.get("/api/offers/"+token)
    assert external.status_code==200,external.text
    data=external.json()
    assert data["stops"]==2
    assert data["tasks"]==3
    assert len(data["itinerary"])==2
    assert data["itinerary"][0]["address"]=="200 Example Rd, Aurora, IL 60505"
    assert data["road_miles"]==50.0
    assert "price_breakdown" not in data
    assert client.post("/api/offers/"+token+"/respond",json={
        "action":"accept","legal_name":"Fictional Test Worker",
        "certified":True,
        "acknowledgment_version":data["acknowledgment_version"]
    }).status_code==200

def test_duplicate_task_claim_is_rejected_and_quote_does_not_claim(client,road):
    # Pricing a route does not hold tasks.
    assert client.post("/api/dispatch/quote",json=sample("Quote only")).status_code==200
    first=client.post("/api/dispatch/routes",json=sample("First")).json()
    assert first["route"]["status"]=="ready"
    second=client.post("/api/dispatch/routes",json=sample("Duplicate"))
    assert second.status_code==409,second.text
    # Identifiers are compared without whitespace and case differences.
    payload=sample("Other",task_id=" task-100 ")
    payload["tasks"]=payload["tasks"][:1]
    # Fixture expects two distinct physical stops; use full task list.
    payload["tasks"].append(sample()["tasks"][2])
    assert client.post("/api/dispatch/routes",json=payload).status_code==409

def test_cancel_frees_claims_but_preserves_history(client,road):
    original=client.post("/api/dispatch/routes",json=sample("Cancel this")).json()["route"]
    cancelled=client.post(f"/api/dispatch/routes/{original['id']}/cancel",
                          json={"expected_version":original["version"]})
    assert cancelled.status_code==200,cancelled.text
    assert cancelled.json()["status"]=="cancelled"
    history=client.get(f"/api/dispatch/routes/{original['id']}")
    assert history.status_code==200
    assert history.json()["status"]=="cancelled"
    reassign=client.post("/api/dispatch/routes",json=sample("Reassigned"))
    assert reassign.status_code==201,reassign.text
    assert reassign.json()["route"]["id"]!=original["id"]

def test_offered_route_must_be_revoked_before_cancel(client,road):
    route=client.post("/api/dispatch/routes",json=sample("Revoked")).json()["route"]
    offer=client.post(f"/api/routes/{route['id']}/offers").json()
    reject=client.post(f"/api/dispatch/routes/{route['id']}/cancel",
                       json={"expected_version":offer["route"]["version"]})
    assert reject.status_code==409
    revoked=client.post(f"/api/routes/{route['id']}/offers/revoke")
    assert revoked.status_code==200,revoked.text
    cancel=client.post(f"/api/dispatch/routes/{route['id']}/cancel",
                       json={"expected_version":revoked.json()["version"]})
    assert cancel.status_code==200
    assert client.get("/api/offers/"+offer["token"]).status_code==410

def test_invalid_duplicate_ids_never_call_road_provider(client,road):
    payload=sample("Bad input")
    payload["tasks"][1]["external_task_id"]=" task-100 "
    response=client.post("/api/dispatch/quote",json=payload)
    assert response.status_code==422
    assert not road

def test_cancelled_route_cannot_be_offered(client,road):
    route=client.post("/api/dispatch/routes",json=sample("Cancelled")).json()["route"]
    assert client.post(f"/api/dispatch/routes/{route['id']}/cancel",
        json={"expected_version":route["version"]}).status_code==200
    assert client.post(f"/api/routes/{route['id']}/offers").status_code==409

def test_unplanned_route_has_no_itinerary(client,road):
    from test_app import create_route
    legacy=create_route(client,title="Legacy route")
    assert client.get(f"/api/dispatch/routes/{legacy['id']}").status_code==404
