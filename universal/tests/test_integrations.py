"""Commercial tenant boundaries for encrypted API connector management."""
import importlib
import re
import sys
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text


def login(client, email, capsys):
    assert client.post("/api/auth/request-link", json={"email": email}).status_code == 200
    match = re.search(r"login_token=([A-Za-z0-9_-]+)", capsys.readouterr().out)
    assert match
    assert client.post("/api/auth/redeem", json={"token": match.group(1)}).status_code == 200


def settings(token):
    return dict(source="crm", display_name="Fictional API", provider="rest_json",
                url="https://api.example.org/workers", api_token=token,
                records_path="data.workers",
                mapping={"external_id": "id", "name": "name",
                         "latitude": "lat", "longitude": "lon", "status": "status"},
                status_map={"approved": "active"})


def test_tenant_connectors_encrypted_and_isolated(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DCC_ENV", "development")
    monkeypatch.setenv("DCC_OWNER_EMAIL", "alpha@example.test")
    monkeypatch.setenv("DCC_ORGANIZATION_SLUG", "alpha")
    monkeypatch.setenv("DCC_DATABASE_URL", f"sqlite:///{tmp_path}/connections.db")
    monkeypatch.setenv("DCC_CONNECTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("DCC_CONTRACTOR_CONNECTORS_JSON", raising=False)
    monkeypatch.delenv("DCC_SMTP_HOST", raising=False)
    module = importlib.reload(sys.modules["app"]) if "app" in sys.modules else importlib.import_module("app")
    integrations = importlib.import_module("integrations")
    with TestClient(module.app) as a, TestClient(module.app) as b:
        with module.engine.begin() as db:
            db.execute(text("INSERT INTO dcc_staff_identities (email) VALUES ('bravo@example.test')"))
            db.execute(text("""INSERT INTO dcc_staff_memberships (identity_id, organization, role)
               SELECT id, 'bravo', 'owner' FROM dcc_staff_identities
               WHERE email='bravo@example.test'"""))
        assert a.get("/api/integrations").status_code == 401
        login(a, "alpha@example.test", capsys)
        login(b, "bravo@example.test", capsys)
        token_a, token_b = "alpha-test-token-0123456789", "bravo-test-token-9876543210"
        assert a.post("/api/integrations", json=settings(token_a)).status_code == 201
        assert b.post("/api/integrations", json=settings(token_b)).status_code == 201
        la = a.get("/api/integrations").json()["connections"]
        lb = b.get("/api/integrations").json()["connections"]
        assert len(la) == len(lb) == 1
        assert token_a not in str(la) and token_b not in str(la)
        assert la[0]["verified"] is False
        with module.engine.connect() as db:
            encrypted = db.execute(text("SELECT encrypted_token FROM dcc_company_integrations")).all()
        assert len(encrypted) == 2
        assert all(token_a not in value and token_b not in value for (value,) in encrypted)
        def fake_fetch(config, token):
            return [dict(external_id="7", name="Alpha Worker" if token == token_a else "Bravo Worker",
                         email=None, status="active", home={"lat": 41.88, "lon": -87.63},
                         home_address="Fictional Place", service_radius_miles=100)]
        monkeypatch.setattr(integrations, "fetch_with_credentials", fake_fetch)
        assert a.post("/api/integrations/crm/test").json()["records_received"] == 1
        assert a.get("/api/contractors").json() == []
        assert a.post("/api/integrations/crm/preview").json()["create"] == 1
        assert a.get("/api/contractors").json() == []
        assert a.post("/api/integrations/crm/sync").json()["create"] == 1
        assert a.post("/api/integrations/crm/sync").json()["unchanged"] == 1
        assert b.get("/api/contractors").json() == []
        assert b.post("/api/integrations/crm/sync").json()["create"] == 1
        assert a.get("/api/contractors").json()[0]["name"] == "Alpha Worker"
        assert b.get("/api/contractors").json()[0]["name"] == "Bravo Worker"
        edit = settings(None)
        edit["display_name"] = "Updated API"
        assert a.put("/api/integrations/crm", json=edit).status_code == 200
        assert a.post("/api/integrations/crm/test").status_code == 200
        assert a.delete("/api/integrations/crm").status_code == 200
        assert a.get("/api/integrations").json()["connections"] == []
        assert b.get("/api/integrations").json()["connections"][0]["source"] == "crm"
        assert a.get("/api/contractors").json()[0]["name"] == "Alpha Worker"


def test_connector_rejects_unsafe_urls_and_non_admin(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DCC_ENV", "development")
    monkeypatch.setenv("DCC_OWNER_EMAIL", "owner@example.test")
    monkeypatch.setenv("DCC_ORGANIZATION_SLUG", "example")
    monkeypatch.setenv("DCC_DATABASE_URL", f"sqlite:///{tmp_path}/unsafe.db")
    monkeypatch.setenv("DCC_CONNECTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("DCC_CONTRACTOR_CONNECTORS_JSON", raising=False)
    monkeypatch.delenv("DCC_SMTP_HOST", raising=False)
    module = importlib.reload(sys.modules["app"]) if "app" in sys.modules else importlib.import_module("app")
    with TestClient(module.app) as owner, TestClient(module.app) as staff:
        login(owner, "owner@example.test", capsys)
        for url in ["http://api.example.org/workers", "https://localhost/data",
                    "https://127.0.0.1/data", "https://10.0.0.1/api",
                    "https://api.example.org:8000/data"]:
            invalid = settings("owner-token-123456789")
            invalid["url"] = url
            assert owner.post("/api/integrations", json=invalid).status_code == 422
        with module.engine.begin() as db:
            db.execute(text("INSERT INTO dcc_staff_identities (email) VALUES ('staff@example.test')"))
            db.execute(text("""INSERT INTO dcc_staff_memberships (identity_id, organization, role)
               SELECT id, 'example', 'dispatcher' FROM dcc_staff_identities
               WHERE email='staff@example.test'"""))
        login(staff, "staff@example.test", capsys)
        assert staff.get("/api/integrations").status_code == 403
        assert staff.post("/api/integrations", json=settings("staff-token-123456789")).status_code == 403
