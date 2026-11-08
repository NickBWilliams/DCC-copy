"""Company-specific contractor integrations managed from the private workspace.

The same application serves many customers, but credentials, mappings, source
identity, import history and roster writes are scoped to an authorized company.

The first managed provider is a generic HTTPS JSON API using a Bearer token.
Other vendors should be implemented as specific adapters, not falsely labeled
as connected integrations. Production is not ready until an approved HTTPS
host allowlist, strong secret management and tenant security audits are in use.
"""
import json
import os
import re
from datetime import datetime, timezone
from typing import Literal

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from contractor_sources import (
    _trusted_url, configured_sources, fetch_with_credentials, source_key
)

FIELD_KEYS = frozenset({
    "external_id", "name", "email", "status", "home_address",
    "latitude", "longitude", "service_radius_miles"
})
REQUIRED_FIELDS = frozenset({"external_id", "name", "latitude", "longitude"})
PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*){0,5}$")
MAX_CONNECTIONS = 25


def utcnow():
    return datetime.now(timezone.utc)


def _require_cipher():
    """Never fall back to a hard-coded key or a key derived from a user ID."""
    raw = os.getenv("DCC_CONNECTOR_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise HTTPException(503, "Connector encryption key not yet configured on this server")
    try:
        return Fernet(raw.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise HTTPException(503, "Connector encryption key is invalid") from exc


def _clean_source(source):
    try:
        return source_key(source)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _valid_url(url):
    try:
        normalized, hostname = _trusted_url(url)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "API endpoint must be a public HTTPS address on port 443") from exc
    allowed = {h.strip().lower().rstrip(".") for h in os.getenv(
        "DCC_CONNECTOR_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if os.getenv("DCC_ENV") != "development" and not allowed:
        raise HTTPException(503, "Production API connector host allowlist is not configured")
    if allowed and hostname not in allowed:
        raise HTTPException(422, "This API hostname is not on the deployment's approved list")
    return normalized


def _validate_mapping(mapping):
    if not isinstance(mapping, dict) or not REQUIRED_FIELDS.issubset(mapping):
        raise HTTPException(422, "Map the contractor ID, name, latitude, and longitude fields")
    if not set(mapping).issubset(FIELD_KEYS):
        raise HTTPException(422, "Unknown field mapping target")
    for field, path in mapping.items():
        if not isinstance(path, str) or not PATH_RE.fullmatch(path):
            raise HTTPException(422, f"Invalid field path for {field}")
    return mapping


def _validate_status_map(mapping):
    if not isinstance(mapping, dict) or len(mapping) > 40:
        raise HTTPException(422, "Invalid status mapping")
    normalized = {}
    for origin, target in mapping.items():
        if (not isinstance(origin, str) or not origin.strip()
                or len(origin) > 60 or target not in ("active", "in_training", "inactive")):
            raise HTTPException(422, "Status mapping must target active, in_training or inactive")
        normalized[origin.strip().lower()] = target
    return normalized


class IntegrationSettings(BaseModel):
    source: str = Field(min_length=2, max_length=32)
    display_name: str = Field(min_length=2, max_length=100)
    provider: Literal["rest_json"] = "rest_json"
    url: str = Field(min_length=12, max_length=500)
    api_token: str | None = Field(default=None, max_length=4096)
    records_path: str = Field(default="contractors", max_length=128)
    mapping: dict[str, str]
    status_map: dict[str, Literal["active", "in_training", "inactive"]] = Field(default_factory=dict)


def install(app, Base, engine, require_staff, import_action, import_batch_type):
    class Integration(Base):
        __tablename__ = "dcc_company_integrations"
        __table_args__ = (UniqueConstraint("organization", "source"),)
        id: Mapped[int] = mapped_column(primary_key=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        source: Mapped[str] = mapped_column(String(32))
        display_name: Mapped[str] = mapped_column(String(100))
        provider: Mapped[str] = mapped_column(String(30), default="rest_json")
        url: Mapped[str] = mapped_column(String(500))
        encrypted_token: Mapped[str] = mapped_column(Text)
        records_path: Mapped[str] = mapped_column(String(128))
        mapping_json: Mapped[str] = mapped_column(Text)
        status_map_json: Mapped[str] = mapped_column(Text)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
        updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
        last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        last_sync_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    class IntegrationEvent(Base):
        __tablename__ = "dcc_company_integration_events"
        id: Mapped[int] = mapped_column(primary_key=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        source: Mapped[str] = mapped_column(String(32), index=True)
        action: Mapped[str] = mapped_column(String(32))
        staff_email: Mapped[str] = mapped_column(String(254))
        at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def admin_only(staff):
        if staff.role not in ("owner", "admin"):
            raise HTTPException(403, "Company owner or admin access required")

    def audit(db, staff, source, action):
        db.add(IntegrationEvent(organization=staff.organization, source=source,
                                action=action, staff_email=staff.email))

    def serialize(row):
        # Credentials are never returned, even to the account owner.
        return {
            "source": row.source,
            "display_name": row.display_name,
            "provider": row.provider,
            "url": row.url,
            "records_path": row.records_path,
            "mapping": json.loads(row.mapping_json),
            "status_map": json.loads(row.status_map_json),
            "credentials_configured": bool(row.encrypted_token),
            "verified": row.last_tested_at is not None,
            "last_tested_at": row.last_tested_at.isoformat() if row.last_tested_at else None,
            "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
            "last_sync_summary": json.loads(row.last_sync_summary_json) if row.last_sync_summary_json else None,
        }

    def load(db, staff, source):
        row = db.scalar(select(Integration).where(
            Integration.organization == staff.organization,
            Integration.source == _clean_source(source)))
        if row is None:
            raise HTTPException(404, "This workspace has no such integration")
        return row

    def checked(settings, creating, staff):
        source = _clean_source(settings.source)
        if source in configured_sources(staff.organization):
            raise HTTPException(409, "A server-managed source already uses this name")
        url = _valid_url(settings.url)
        if settings.records_path and not PATH_RE.fullmatch(settings.records_path):
            raise HTTPException(422, "Records path must be a dotted JSON field path")
        mapping = _validate_mapping(settings.mapping)
        statuses = _validate_status_map(settings.status_map)
        token = settings.api_token
        if token is not None:
            token = token.strip()
            if token and (len(token) < 8 or any(char in token for char in "\r\n\x00")):
                raise HTTPException(422, "API token must be at least 8 characters and contain no line breaks")
        if creating and not token:
            raise HTTPException(422, "An authorized read-only API bearer token is required")
        return source, url, mapping, statuses, token

    @app.get("/api/integrations")
    def list_integrations(staff=Depends(require_staff)):
        admin_only(staff)
        with Session(engine) as db:
            rows = list(db.scalars(select(Integration).where(
                Integration.organization == staff.organization).order_by(Integration.display_name)))
            return {
                "connections": [serialize(row) for row in rows],
                "credential_storage_ready": bool(os.getenv("DCC_CONNECTOR_ENCRYPTION_KEY", "").strip()),
                "notice": "Connections and credentials are private to this company. No source is synced automatically.",
            }

    @app.post("/api/integrations", status_code=201)
    def add_integration(settings: IntegrationSettings, staff=Depends(require_staff)):
        admin_only(staff)
        source, url, mapping, statuses, token = checked(settings, True, staff)
        cipher = _require_cipher()
        with Session(engine) as db:
            count = len(list(db.scalars(select(Integration.id).where(
                Integration.organization == staff.organization))))
            if count >= MAX_CONNECTIONS:
                raise HTTPException(422, "Maximum number of configured connections reached")
            existing = db.scalar(select(Integration).where(
                Integration.organization == staff.organization, Integration.source == source))
            if existing:
                raise HTTPException(409, "An integration with this source ID already exists")
            row = Integration(
                organization=staff.organization, source=source,
                display_name=settings.display_name.strip(), provider=settings.provider,
                url=url, encrypted_token=cipher.encrypt(token.encode()).decode("ascii"),
                records_path=settings.records_path,
                mapping_json=json.dumps(mapping, separators=(",", ":")),
                status_map_json=json.dumps(statuses, separators=(",", ":")))
            db.add(row)
            audit(db, staff, source, "created")
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise HTTPException(409, "Source ID was registered by another request") from exc
            return serialize(row)

    @app.put("/api/integrations/{source}")
    def edit_integration(source: str, settings: IntegrationSettings,
                         staff=Depends(require_staff)):
        admin_only(staff)
        cleaned, url, mapping, statuses, token = checked(settings, False, staff)
        if cleaned != _clean_source(source):
            raise HTTPException(422, "A saved integration's source ID cannot change")
        with Session(engine) as db:
            row = load(db, staff, source)
            row.display_name = settings.display_name.strip()
            row.url = url
            row.records_path = settings.records_path
            row.mapping_json = json.dumps(mapping, separators=(",", ":"))
            row.status_map_json = json.dumps(statuses, separators=(",", ":"))
            if token:
                row.encrypted_token = _require_cipher().encrypt(token.encode()).decode("ascii")
            row.last_tested_at = None  # Force re-validation of new config.
            row.updated_at = utcnow()
            audit(db, staff, cleaned, "configured")
            db.commit()
            return serialize(row)

    def pull_records(staff, source):
        # No unauthenticated webhook, arbitrary URL, or caller-provided tenant ID.
        with Session(engine) as db:
            row = load(db, staff, source)
            config = {
                "url": _valid_url(row.url),
                "records_path": row.records_path,
                "mapping": json.loads(row.mapping_json),
                "status_map": json.loads(row.status_map_json),
            }
            try:
                credential = _require_cipher().decrypt(row.encrypted_token.encode()).decode("utf-8")
            except (InvalidToken, UnicodeError) as exc:
                raise HTTPException(503, "Integration credential could not be decrypted") from exc
        try:
            return fetch_with_credentials(config, credential)
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            # Prevent API responses, URLs, bearer secrets or upstream PII
            # from surfacing in user-visible exception bodies.
            raise HTTPException(502, f"External provider fetch failed ({type(exc).__name__})") from exc

    def to_batch(source, fetched):
        if not fetched:
            return None
        try:
            return import_batch_type(source=source, contractors=fetched)
        except ValidationError as exc:
            raise HTTPException(422, "The source records don't fit the mapped contractor schema") from exc

    @app.post("/api/integrations/{source}/test")
    def test_connection(source: str, staff=Depends(require_staff)):
        admin_only(staff)
        source = _clean_source(source)
        data = pull_records(staff, source)
        with Session(engine) as db:
            row = load(db, staff, source)
            row.last_tested_at = utcnow()
            row.updated_at = utcnow()
            audit(db, staff, source, "tested")
            db.commit()
        return {
            "verified": True,
            "records_received": len(data),
            "sample": [{"external_id": d["external_id"], "name": d["name"],
                        "status": d["status"]} for d in data[:5]],
            "notice": "Read-only connection test. No company roster was changed.",
        }

    @app.post("/api/integrations/{source}/preview")
    def preview_connection(source: str, staff=Depends(require_staff)):
        admin_only(staff)
        source = _clean_source(source)
        records = pull_records(staff, source)
        if not records:
            return {"source": source, "total": 0, "create": 0, "update": 0,
                    "unchanged": 0, "applied": False,
                    "notice": "Empty upstream list: no changes proposed."}
        batch = to_batch(source, records)
        return import_action(batch, staff, False)

    @app.post("/api/integrations/{source}/sync")
    def sync_connection(source: str, staff=Depends(require_staff)):
        admin_only(staff)
        source = _clean_source(source)
        records = pull_records(staff, source)
        if not records:
            return {"source": source, "total": 0, "create": 0, "update": 0,
                    "unchanged": 0, "applied": False,
                    "notice": "Upstream returned no records; nothing changed."}
        batch = to_batch(source, records)
        summary = import_action(batch, staff, True)
        with Session(engine) as db:
            row = load(db, staff, source)
            row.last_tested_at = utcnow()
            row.last_synced_at = utcnow()
            row.last_sync_summary_json = json.dumps(summary, separators=(",", ":"))
            audit(db, staff, source, "synced")
            db.commit()
        return summary

    @app.get("/api/integrations/{source}/history")
    def connection_history(source: str, staff=Depends(require_staff)):
        admin_only(staff)
        with Session(engine) as db:
            load(db, staff, source)
            events = db.scalars(select(IntegrationEvent).where(
                IntegrationEvent.organization == staff.organization,
                IntegrationEvent.source == _clean_source(source)).order_by(
                    IntegrationEvent.id.desc()).limit(50)).all()
            return [{"action": e.action, "at": e.at.isoformat(),
                     "by": e.staff_email} for e in events]

    @app.delete("/api/integrations/{source}")
    def disconnect(source: str, staff=Depends(require_staff)):
        admin_only(staff)
        with Session(engine) as db:
            row = load(db, staff, source)
            key = row.source
            db.delete(row)
            audit(db, staff, key, "disconnected")
            db.commit()
        return {"disconnected": key,
                "notice": "Integration credentials disconnected. Previously imported contractors were retained."}
