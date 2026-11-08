"""Independent company-branded contractor offer acceptance and audit tests."""
import importlib
import re
import sys

from fastapi.testclient import TestClient
from sqlalchemy import text


def login(client, address, capsys):
    assert client.post("/api/auth/request-link", json={"email": address}).status_code == 200
    token = re.search(r"login_token=([A-Za-z0-9_-]+)", capsys.readouterr().out)
    assert token is not None
    signed = client.post("/api/auth/redeem", json={"token": token.group(1)})
    assert signed.status_code == 200, signed.text


def new_offer(client, label="Fictional Route"):
    created = client.post("/api/routes", json={
        "title": label, "contractor": "Demo Person",
        "stops": 2, "tasks": 3, "price": "78.50",
    })
    assert created.status_code == 201, created.text
    route = created.json()
    ready = client.post(f"/api/routes/{route['id']}/transition", json={
        "action": "prepare", "expected_version": route["version"],
    })
    assert ready.status_code == 200, ready.text
    issued = client.post(f"/api/routes/{route['id']}/offers", json={
        "due_date": "2099-01-01", "work_order_label": "DEMO-WO-100",
    })
    assert issued.status_code == 201, issued.text
    return route["id"], issued.json()


def test_contractor_offer_display_signature_version_and_receipt(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DCC_ENV", "development")
    monkeypatch.setenv("DCC_OWNER_EMAIL", "example-owner@example.test")
    monkeypatch.setenv("DCC_ORGANIZATION_SLUG", "company-alpha")
    monkeypatch.setenv("DCC_DATABASE_URL", f"sqlite:///{tmp_path}/offers.sqlite3")
    monkeypatch.setenv("DCC_PUBLIC_URL", "http://localhost:8000")
    monkeypatch.delenv("DCC_SMTP_HOST", raising=False)
    module = importlib.reload(sys.modules["app"]) if "app" in sys.modules else importlib.import_module("app")
    with TestClient(module.app) as client:
        login(client, "example-owner@example.test", capsys)
        settings = client.put("/api/offer-settings", json={
            "display_name": "Independent Field Services",
            "acknowledgment_text": "I have reviewed every listed stop and agree to carry out the proposed service work.",
        })
        assert settings.status_code == 200, settings.text
        assert settings.json()["acknowledgment_version"] == 1
        route_id, offer = new_offer(client)
        assert offer["delivery"] == "manual_only"
        assert "DEMO-WO-100" in offer["email_preview"]["subject"]
        assert "http://localhost:8000/offer/" in offer["email_preview"]["body"]
        assert offer["expires_at"]
        token = offer["token"]
        api = f"/api/offers/{token}"
        viewed = client.get(api)
        assert viewed.status_code == 200, viewed.text
        body = viewed.json()
        assert body["company_name"] == "Independent Field Services"
        assert body["due_date"] == "2099-01-01"
        assert body["work_order_label"] == "DEMO-WO-100"
        assert body["price"] == "78.50"
        assert "reviewed every" in body["acknowledgment_text"]
        assert body["acknowledgment_version"] == 1
        assert client.get(f"/offer/{token}").status_code == 200

        # Required confirmation must be enforced on the server, not JS alone.
        assert client.post(api + "/respond", json={"action": "accept"}).status_code == 422
        assert client.post(api + "/respond", json={
            "action": "accept", "legal_name": "Demo Signer", "certified": False,
            "acknowledgment_version": 1,
        }).status_code == 422
        assert client.post(api + "/respond", json={
            "action": "accept", "legal_name": "Demo Signer", "certified": True,
            "acknowledgment_version": 5,
        }).status_code == 409
        accepted = client.post(api + "/respond", json={
            "action": "accept", "legal_name": "Demo Signer",
            "certified": True, "acknowledgment_version": 1,
            "notes": "I can complete these jobs.",
        })
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "accepted"
        assert accepted.json()["accepted_by"] == "Demo Signer"
        assert client.post(api + "/respond", json={
            "action": "accept", "legal_name": "Other", "certified": True,
            "acknowledgment_version": 1,
        }).status_code == 410
        with module.engine.connect() as conn:
            decision = conn.execute(text("""
                SELECT organization, route_id, action, legal_name, certified,
                       acknowledgment_text, acknowledgment_version, notes
                FROM dcc_offer_decisions WHERE route_id = :route_id
            """), {"route_id": route_id}).one()
        assert decision.organization == "company-alpha"
        assert decision.action == "accept"
        assert decision.legal_name == "Demo Signer"
        assert decision.certified
        assert decision.acknowledgment_version == 1
        assert decision.notes == "I can complete these jobs."


def test_company_branding_is_tenant_scoped_and_terms_are_versioned(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DCC_ENV", "development")
    monkeypatch.setenv("DCC_OWNER_EMAIL", "alpha-owner@example.test")
    monkeypatch.setenv("DCC_ORGANIZATION_SLUG", "company-alpha")
    monkeypatch.setenv("DCC_DATABASE_URL", f"sqlite:///{tmp_path}/brands.sqlite3")
    monkeypatch.delenv("DCC_SMTP_HOST", raising=False)
    module = importlib.reload(sys.modules["app"]) if "app" in sys.modules else importlib.import_module("app")
    with TestClient(module.app) as a, TestClient(module.app) as b:
        with module.engine.begin() as conn:
            conn.execute(text("INSERT INTO dcc_staff_identities (email) VALUES ('bravo-owner@example.test')"))
            conn.execute(text("""INSERT INTO dcc_staff_memberships (identity_id, organization, role)
                SELECT id, 'company-bravo', 'owner'
                FROM dcc_staff_identities WHERE email='bravo-owner@example.test'"""))
        login(a, "alpha-owner@example.test", capsys)
        login(b, "bravo-owner@example.test", capsys)
        updated = a.put("/api/offer-settings", json={
            "display_name": "Alpha Services",
            "acknowledgment_text": "I agree to accept and complete the route details listed above as confirmed.",
        })
        assert updated.status_code == 200
        second = a.put("/api/offer-settings", json={
            "display_name": "Alpha Services",
            "acknowledgment_text": "I confirm I reviewed all work locations and tasks and agree to accept the route.",
        })
        assert second.status_code == 200
        assert second.json()["acknowledgment_version"] == 2
        assert b.get("/api/offer-settings").json()["display_name"] == "DCC Universal"
        rid, issued = new_offer(a)
        assert b.post(f"/api/routes/{rid}/offers").status_code == 404
        public = b.get("/api/offers/"+issued["token"])
        assert public.status_code == 200
        assert public.json()["company_name"] == "Alpha Services"
        assert public.json()["acknowledgment_version"] == 2


def test_counter_and_decline_record_notes_without_legal_signature(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DCC_ENV", "development")
    monkeypatch.setenv("DCC_OWNER_EMAIL", "owner@example.test")
    monkeypatch.setenv("DCC_ORGANIZATION_SLUG", "fictional")
    monkeypatch.setenv("DCC_DATABASE_URL", f"sqlite:///{tmp_path}/reply.sqlite3")
    monkeypatch.delenv("DCC_SMTP_HOST", raising=False)
    module = importlib.reload(sys.modules["app"]) if "app" in sys.modules else importlib.import_module("app")
    with TestClient(module.app) as client:
        login(client, "owner@example.test", capsys)
        _, first = new_offer(client, "First demo")
        token = first["token"]
        assert client.post(f"/api/offers/{token}/respond", json={
            "action": "counter", "amount": "94.00",
            "notes": "Additional travel required",
        }).json()["status"] == "countered"
        _, second = new_offer(client, "Second demo")
        assert client.post(f"/api/offers/{second['token']}/respond", json={
            "action": "decline", "notes": "Already booked",
        }).json()["status"] == "declined"
        with module.engine.connect() as conn:
            decisions = conn.execute(text("""
                SELECT action, legal_name, notes
                FROM dcc_offer_decisions ORDER BY id
            """)).all()
        assert decisions[0].action == "counter" and decisions[0].legal_name is None
        assert decisions[0].notes == "Additional travel required"
        assert decisions[1].action == "decline" and decisions[1].notes == "Already booked"
