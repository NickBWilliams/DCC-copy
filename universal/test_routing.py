"""Independent route-planning behavior; all external calls are mocked."""
import pytest

from test_app import client
import routing

HOME = (-87.6298, 41.8781)
STOPS = [(-87.6198, 41.8850), (-87.6755, 41.8920), (-87.6500, 41.8600)]

def test_geographic_order_keeps_every_stop_once():
    for size in (1, 2, 3, 20, 60):
        points = [(-87.7+i*.001, 41.8+(i%7)*.005) for i in range(size)]
        order = routing.geographic_order(HOME, points)
        assert sorted(order) == list(range(size))
    assert routing.geographic_order(HOME, STOPS) == routing.geographic_order(HOME, STOPS)

def test_osrm_full_roundtrip(monkeypatch):
    def get(url, *, params=None):
        assert "/trip/v1/driving/" in url
        assert params["roundtrip"] == "true"
        assert params["source"] == "first"
        return {"code":"Ok",
                "trips":[{"distance":210000, "duration":12600}],
                "waypoints":[{"waypoint_index":0},{"waypoint_index":2},
                             {"waypoint_index":1},{"waypoint_index":3}]}
    monkeypatch.setattr(routing,"_get_json",get)
    trip=routing._osrm_roundtrip(HOME,STOPS,"https://example.invalid")
    assert trip["stop_order"] == [1,0,2]
    assert trip["road_miles"] > 100
    assert trip["driving_hours"] == 3.5

def test_heigit_optimization_and_directions(monkeypatch):
    called=[]
    def post(url, *, payload, headers=None):
        called.append(url)
        assert headers["Authorization"] == "private-test-key"
        if url == routing.HEIGIT_OPTIMIZATION:
            return {"code":0,"routes":[{"steps":[
                {"type":"start"},{"type":"job","job":2},{"type":"job","job":1},
                {"type":"job","job":3},{"type":"end"}]}]}
        assert url == routing.HEIGIT_DIRECTIONS
        assert payload["coordinates"][0] == list(HOME)
        assert payload["coordinates"][-1] == list(HOME)
        return {"routes":[{"summary":{"distance":10000,"duration":1800}}]}
    monkeypatch.setattr(routing,"_post_json",post)
    trip=routing._heigit_roundtrip(HOME,STOPS,"private-test-key")
    assert trip["stop_order"] == [1,0,2]
    assert trip["driving_hours"] == 0.5
    assert called == [routing.HEIGIT_OPTIMIZATION,routing.HEIGIT_DIRECTIONS]

def test_heigit_long_route_is_one_contiguous_trip(monkeypatch):
    directions=[]
    def post(url, *, payload, headers=None):
        assert url == routing.HEIGIT_DIRECTIONS
        directions.append(payload["coordinates"])
        return {"routes":[{"summary":{"distance":10000,"duration":1800}}]}
    monkeypatch.setattr(routing,"_post_json",post)
    stops=[(-87.7+i*.001, 41.8+(i%7)*.004) for i in range(60)]
    trip=routing._heigit_roundtrip(HOME,stops,"private-test-key")
    assert sorted(trip["stop_order"]) == list(range(60))
    assert len(directions) == 2
    assert directions[0][0] == list(HOME)
    assert directions[0][-1] == directions[1][0]
    assert directions[-1][-1] == list(HOME)

def test_missing_personal_route_provider_is_not_silently_mapbox(monkeypatch):
    monkeypatch.delenv("HEIGIT_API_KEY", raising=False)
    monkeypatch.delenv("DCC_OSRM_BASE_URL", raising=False)
    monkeypatch.delenv("DCC_ALLOW_PUBLIC_OSRM", raising=False)
    with pytest.raises(routing.RoutingUnavailable):
        routing.active_provider()

def test_staff_route_api_and_cache(client, monkeypatch):
    monkeypatch.setenv("DCC_OSRM_BASE_URL","https://private-osrm.invalid")
    called=[]
    def calculate(home,stops):
        called.append((home,stops))
        return {"road_miles":45.2,"driving_hours":1.4,
                "stop_order":[1,0],"provider":"osrm"}
    monkeypatch.setattr(routing,"roundtrip",calculate)
    data={"home":{"lon":-87.62,"lat":41.88},
          "stops":[{"lon":-87.61,"lat":41.9},{"lon":-87.70,"lat":41.89}]}
    one=client.post("/api/routing/roundtrip",json=data)
    assert one.status_code == 200, one.text
    assert one.json()["cached"] is False
    two=client.post("/api/routing/roundtrip",json=data)
    assert two.status_code == 200, two.text
    assert two.json()["cached"] is True
    assert len(called)==1

def test_census_strict_address_and_cache(client, monkeypatch):
    called=[]
    def census(url,*,params=None):
        called.append(url)
        return {"result":{"addressMatches":[{"coordinates":{"x":-87.6,"y":41.9},
            "addressComponents":{"state":"IL","zip":"60601"}}]}}
    monkeypatch.setattr(routing,"_get_json",census)
    address={"address":"123 Example Street, Chicago, IL 60601"}
    first=client.post("/api/routing/geocode",json=address)
    assert first.status_code == 200, first.text
    assert first.json()["source"] == "us-census-address"
    assert client.post("/api/routing/geocode",json=address).json()["cached"] is True
    assert len(called)==1
    with pytest.raises(routing.RoutingUnavailable):
        routing.census_geocode("123 Example Street, Chicago, NY 10001")
