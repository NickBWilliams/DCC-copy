"""Independent contractor matching. No outside routing API is called."""
import pytest
from sqlalchemy import delete, insert

from test_app import client

@pytest.fixture(autouse=True)
def isolate_roster():
    from app import Base, engine
    with engine.begin() as conn:
        conn.execute(delete(Base.metadata.tables["dcc_contractor_roster_events"]))
        conn.execute(delete(Base.metadata.tables["dcc_contractor_roster"]))
    yield

def contractor(id="IC-001", name="Chicago Contractor", lat=41.8781, lon=-87.6298,
               status="active", radius=100):
    return {"external_id":id,"name":name,"email":"contractor@example.test",
            "status":status,"home":{"lat":lat,"lon":lon},
            "service_radius_miles":radius}

def test_create_duplicate_rejected_and_list_company_scoped(client):
    first=client.post("/api/contractors",json=contractor())
    assert first.status_code==201,first.text
    again=client.post("/api/contractors",json=contractor(" ic-001 "))
    assert again.status_code==409
    roster=client.get("/api/contractors").json()
    assert len(roster)==1
    assert roster[0]["name"]=="Chicago Contractor"
    anonymous=__import__("fastapi").testclient.TestClient(__import__("app").app)
    assert anonymous.get("/api/contractors").status_code==401

def test_geographic_shortlist_and_radius_are_clearly_not_road_miles(client):
    for data in [
        contractor("near","Near",41.8781,-87.6298,radius=20),
        contractor("far","Far",42.3,-87.9,radius=5),
        contractor("training","Training",41.88,-87.64,status="in_training",radius=20),
        contractor("inactive","Inactive",41.88,-87.64,status="inactive"),
    ]:
        assert client.post("/api/contractors",json=data).status_code==201
    nearby=client.get("/api/contractors/nearby",params={"lat":41.8781,"lon":-87.6298})
    assert nearby.status_code==200,nearby.text
    result=nearby.json()
    assert result["distance_type"]=="aerial_shortlist_only"
    assert [c["name"] for c in result["candidates"]]==["Near"]
    all_active=client.get("/api/contractors/nearby",params={
        "lat":41.8781,"lon":-87.6298,"include_outside_radius":"true"})
    assert [c["name"] for c in all_active.json()["candidates"]]==["Near","Far"]
    include_training=client.get("/api/contractors/nearby",params={
        "lat":41.8781,"lon":-87.6298,"include_training":"true"})
    assert [c["name"] for c in include_training.json()["candidates"]]==["Near","Training"]
    assert not any(c["name"]=="Inactive" for c in include_training.json()["candidates"])

def test_roster_update_version_and_deactivation(client):
    created=client.post("/api/contractors",json=contractor()).json()
    payload=contractor(name="Chicago Supervisor",status="inactive")
    payload["expected_version"]=created["version"]
    ok=client.put("/api/contractors/IC-001",json=payload)
    assert ok.status_code==200,ok.text
    assert ok.json()["version"]==2
    assert ok.json()["name"]=="Chicago Supervisor"
    assert client.put("/api/contractors/IC-001",json=payload).status_code==409
    assert client.get("/api/contractors/nearby",params={"lat":41.88,"lon":-87.62}).json()["candidates"]==[]

def test_company_roster_isolation(client):
    from app import Base,engine
    with engine.begin() as conn:
        conn.execute(insert(Base.metadata.tables["dcc_contractor_roster"]).values(
            organization="another-company",contractor_ref="foreign",
            external_id="FOREIGN",name="Outside Contractor",email=None,status="active",
            home_lat=41.8781,home_lon=-87.6298,service_radius_miles=200,version=1))
    assert client.get("/api/contractors").json()==[]
    assert client.get("/api/contractors/nearby",params={"lat":41.8781,"lon":-87.6298}).json()["candidates"]==[]

def test_invalid_location_is_rejected(client):
    bad=contractor()
    bad["home"]["lat"]=1000
    assert client.post("/api/contractors",json=bad).status_code==422
    assert client.get("/api/contractors/nearby",params={"lat":999,"lon":-87}).status_code==422
    assert client.post("/api/contractors",json=contractor(id="   ")).status_code==422

def test_outside_radius_sort_after_eligible(client):
    assert client.post("/api/contractors",json=contractor(
        "a","Within",41.89,-87.64,radius=2)).status_code==201
    assert client.post("/api/contractors",json=contractor(
        "b","Outside",41.8781,-87.6298,radius=0)).status_code==201
    results=client.get("/api/contractors/nearby",params={
        "lat":41.8781,"lon":-87.6298,"include_outside_radius":"true"}).json()["candidates"]
    assert [x["name"] for x in results]==["Within","Outside"]
    assert results[0]["within_service_radius"] is True
    assert results[1]["within_service_radius"] is False
