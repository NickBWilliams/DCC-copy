"""Private, multi-company contractor directory and low-cost proximity matching.

No contractor accounts, logins or portals are created. Straight-line distance
is used ONLY to shortlist candidates; quote and pay use road-network routing.
"""
import math
from datetime import datetime, timezone
from typing import Literal
from collections import Counter

import httpx
from demo_contractors import demo_rows
from contractor_sources import configured_sources, fetch_contractors, source_descriptions, source_key

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from routing import Point

EARTH_RADIUS_MILES = 3958.7613

def now():
    return datetime.now(timezone.utc)

def ref_key(value):
    result = value.strip().casefold()
    if not result:
        raise HTTPException(422, "Contractor ID cannot be blank")
    return result

def miles_between(lon1, lat1, lon2, lat2):
    values = (lon1, lat1, lon2, lat2)
    if not all(math.isfinite(value) for value in values):
        raise HTTPException(422, "Contractor and route coordinates must be finite")
    p1,p2 = math.radians(lat1),math.radians(lat2)
    dp = p2-p1
    dl = math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(min(1.0,a)))

class NewContractor(BaseModel):
    external_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=254)
    status: Literal["active","in_training","inactive"] = "active"
    home: Point
    service_radius_miles: float = Field(default=100, ge=0, le=5000)

class ContractorUpdate(NewContractor):
    expected_version: int = Field(ge=1)

class ImportRecord(NewContractor):
    home_address: str | None = Field(default=None, max_length=300)

class ImportBatch(BaseModel):
    source: str = Field(min_length=2, max_length=32)
    contractors: list[ImportRecord] = Field(min_length=1, max_length=2000)

def install(app, Base, engine, require_staff):
    class Contractor(Base):
        __tablename__ = "dcc_contractor_roster"
        __table_args__ = (UniqueConstraint("organization","contractor_ref"),)
        id: Mapped[int] = mapped_column(primary_key=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        contractor_ref: Mapped[str] = mapped_column(String(128))
        external_id: Mapped[str] = mapped_column(String(128))
        name: Mapped[str] = mapped_column(String(200))
        email: Mapped[str | None] = mapped_column(String(254), nullable=True)
        status: Mapped[str] = mapped_column(String(20), default="active")
        home_lon: Mapped[float] = mapped_column(Float)
        home_lat: Mapped[float] = mapped_column(Float)
        service_radius_miles: Mapped[float] = mapped_column(Float, default=100)
        version: Mapped[int] = mapped_column(Integer, default=1)
        updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    class ContractorAudit(Base):
        __tablename__ = "dcc_contractor_roster_events"
        id: Mapped[int] = mapped_column(primary_key=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        contractor_ref: Mapped[str] = mapped_column(String(128))
        action: Mapped[str] = mapped_column(String(20))
        staff_email: Mapped[str] = mapped_column(String(254))
        at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    class ContractorOrigin(Base):
        """Additive metadata table: no migration of the existing roster required."""
        __tablename__ = "dcc_contractor_origins"
        __table_args__ = (UniqueConstraint("organization", "contractor_ref"),)
        id: Mapped[int] = mapped_column(primary_key=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        contractor_ref: Mapped[str] = mapped_column(String(128))
        source: Mapped[str] = mapped_column(String(32), index=True)
        source_ref: Mapped[str] = mapped_column(String(128))
        home_address: Mapped[str | None] = mapped_column(String(300), nullable=True)
        is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
        last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    def origins_for(db, organization, refs=None):
        query = select(ContractorOrigin).where(ContractorOrigin.organization == organization)
        if refs is not None:
            if not refs:
                return {}
            query = query.where(ContractorOrigin.contractor_ref.in_(refs))
        return {o.contractor_ref: o for o in db.scalars(query).all()}

    def serialize(row, origin=None):
        return {
            "external_id":row.external_id,"name":row.name,"email":row.email,
            "status":row.status,
            "home":{"lon":row.home_lon,"lat":row.home_lat},
            "home_address":origin.home_address if origin else None,
            "source":origin.source if origin else "manual",
            "is_demo":bool(origin.is_demo) if origin else False,
            "service_radius_miles":row.service_radius_miles,
            "version":row.version,
        }

    def require_owner_or_admin(staff):
        if staff.role not in ("owner", "admin"):
            raise HTTPException(403, "Owner or admin access required for contractor imports")

    def sanitize(data):
        if not data.name.strip() or not data.external_id.strip():
            raise HTTPException(422, "Contractor name and ID must not be blank")
        if not (math.isfinite(data.home.lat) and math.isfinite(data.home.lon)
                and math.isfinite(data.service_radius_miles)):
            raise HTTPException(422, "Nonfinite contractor location or service radius")
        if data.email and ("@" not in data.email or not data.email.strip()):
            raise HTTPException(422, "Invalid contractor email")
        return data

    @app.get("/api/contractors")
    def roster(staff=Depends(require_staff)):
        with Session(engine) as db:
            rows=db.scalars(select(Contractor).where(
                Contractor.organization==staff.organization).order_by(Contractor.name)).all()
            origins = origins_for(db, staff.organization, [r.contractor_ref for r in rows])
            return [serialize(row, origins.get(row.contractor_ref)) for row in rows]

    @app.post("/api/contractors", status_code=201)
    def create_contractor(data:NewContractor,staff=Depends(require_staff)):
        sanitize(data)
        ref=ref_key(data.external_id)
        with Session(engine) as db:
            found=db.scalar(select(Contractor).where(
                Contractor.organization==staff.organization,
                Contractor.contractor_ref==ref))
            if found is not None:
                raise HTTPException(409, "Contractor ID already exists")
            row=Contractor(organization=staff.organization,
                           contractor_ref=ref,external_id=data.external_id.strip(),
                           name=data.name.strip(),email=data.email.strip() if data.email else None,
                           status=data.status,home_lon=data.home.lon,home_lat=data.home.lat,
                           service_radius_miles=data.service_radius_miles)
            db.add(row)
            db.add(ContractorAudit(organization=staff.organization,contractor_ref=ref,
                                   action="created",staff_email=staff.email))
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise HTTPException(409,"Contractor ID already exists") from exc
            db.refresh(row)
            return serialize(row)

    @app.put("/api/contractors/{external_id}")
    def update_contractor(external_id:str,data:ContractorUpdate,staff=Depends(require_staff)):
        sanitize(data)
        ref=ref_key(external_id)
        if ref_key(data.external_id) != ref:
            raise HTTPException(422, "Contractor ID cannot change")
        with Session(engine) as db:
            q=select(Contractor).where(Contractor.organization==staff.organization,
                                       Contractor.contractor_ref==ref)
            if not str(engine.url).startswith("sqlite"):
                q=q.with_for_update()
            row=db.scalar(q)
            if row is None:
                raise HTTPException(404,"Contractor not found")
            if row.version != data.expected_version:
                raise HTTPException(409,"Stale contractor version; refresh before editing")
            row.name=data.name.strip()
            row.email=data.email.strip() if data.email else None
            row.status=data.status
            row.home_lat=data.home.lat
            row.home_lon=data.home.lon
            row.service_radius_miles=data.service_radius_miles
            row.version+=1
            row.updated_at=now()
            db.add(ContractorAudit(organization=staff.organization,contractor_ref=ref,
                                   action="updated",staff_email=staff.email))
            db.commit()
            return serialize(row)

    @app.get("/api/contractors/nearby")
    def nearby(lat:float=Query(ge=-90,le=90),
               lon:float=Query(ge=-180,le=180),
               limit:int=Query(default=12,ge=1,le=100),
               include_training:bool=False,
               include_outside_radius:bool=False,
               staff=Depends(require_staff)):
        if not math.isfinite(lat) or not math.isfinite(lon):
            raise HTTPException(422,"Invalid search coordinates")
        eligible=["active","in_training"] if include_training else ["active"]
        with Session(engine) as db:
            rows=db.scalars(select(Contractor).where(
                Contractor.organization==staff.organization,
                Contractor.status.in_(eligible))).all()
            origins=origins_for(db, staff.organization, [r.contractor_ref for r in rows])
        ranked=[]
        for row in rows:
            distance=miles_between(lon,lat,row.home_lon,row.home_lat)
            inside=distance <= row.service_radius_miles
            if inside or include_outside_radius:
                ranked.append({**serialize(row,origins.get(row.contractor_ref)),"aerial_miles":round(distance,2),
                               "within_service_radius":inside})
        ranked.sort(key=lambda r:(not r["within_service_radius"],
                                   r["aerial_miles"],r["name"].casefold()))
        return {"distance_type":"aerial_shortlist_only",
                "notice":"Not road miles. Final route quotes use real road-network routing.",
                "candidates":ranked[:limit]}


    def _planned_records(data):
        """Normalize incoming stable IDs, reject duplicate keys before modifying data."""
        try:
            source = source_key(data.source)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        planned = []
        refs = set()
        for row in data.contractors:
            sanitize(row)
            external = row.external_id.strip()
            # Source-scoped IDs prevent collisions between different customers'
            # systems and existing manually created contractors.
            combined = f"{source}:{external}"
            if len(combined) > 128:
                raise HTTPException(422, "Source-prefixed contractor ID exceeds 128 characters")
            ref = ref_key(combined)
            if ref in refs:
                raise HTTPException(422, f"Duplicate source contractor ID: {external}")
            refs.add(ref)
            planned.append((ref, combined, external, row))
        return source, planned

    def _same(row, origin, incoming):
        return (
            row.name == incoming.name.strip()
            and row.email == (incoming.email.strip() if incoming.email else None)
            and row.status == incoming.status
            and row.home_lat == incoming.home.lat
            and row.home_lon == incoming.home.lon
            and row.service_radius_miles == incoming.service_radius_miles
            and (origin.home_address or None) == (incoming.home_address.strip() if incoming.home_address else None)
        )

    def _run_import(data, staff, execute):
        require_owner_or_admin(staff)
        source, planned = _planned_records(data)
        totals = {"create": 0, "update": 0, "unchanged": 0}
        with Session(engine) as db:
            requested = [ref for ref, _, _, _ in planned]
            q = select(Contractor).where(Contractor.organization == staff.organization,
                                         Contractor.contractor_ref.in_(requested))
            if execute and not str(engine.url).startswith("sqlite"):
                q = q.with_for_update()
            existing = {r.contractor_ref: r for r in db.scalars(q).all()}
            origins = origins_for(db, staff.organization, requested)
            # Perform the full conflict validation first; never adopt a manual
            # contractor with a coincidentally identical source-prefixed ID.
            for ref, _, _, _ in planned:
                found, origin = existing.get(ref), origins.get(ref)
                if found and (not origin or origin.source != source or origin.is_demo):
                    raise HTTPException(409, f"Contractor ID {ref} belongs to another source or a manual entry")
            for ref, combined, source_ref, incoming in planned:
                row = existing.get(ref)
                origin = origins.get(ref)
                if row is None:
                    totals["create"] += 1
                    if execute:
                        row = Contractor(
                            organization=staff.organization,
                            contractor_ref=ref, external_id=combined,
                            name=incoming.name.strip(),
                            email=incoming.email.strip() if incoming.email else None,
                            status=incoming.status, home_lat=incoming.home.lat,
                            home_lon=incoming.home.lon,
                            service_radius_miles=incoming.service_radius_miles)
                        db.add(row)
                        db.add(ContractorOrigin(
                            organization=staff.organization, contractor_ref=ref,
                            source=source, source_ref=source_ref,
                            home_address=incoming.home_address.strip() if incoming.home_address else None,
                            is_demo=False, last_synced_at=now()))
                        db.add(ContractorAudit(
                            organization=staff.organization, contractor_ref=ref,
                            action="imported", staff_email=staff.email))
                elif _same(row, origin, incoming):
                    totals["unchanged"] += 1
                    if execute:
                        origin.last_synced_at = now()
                else:
                    totals["update"] += 1
                    if execute:
                        row.name = incoming.name.strip()
                        row.email = incoming.email.strip() if incoming.email else None
                        row.status = incoming.status
                        row.home_lat, row.home_lon = incoming.home.lat, incoming.home.lon
                        row.service_radius_miles = incoming.service_radius_miles
                        row.version += 1
                        row.updated_at = now()
                        origin.home_address = incoming.home_address.strip() if incoming.home_address else None
                        origin.last_synced_at = now()
                        db.add(ContractorAudit(
                            organization=staff.organization, contractor_ref=ref,
                            action="synced", staff_email=staff.email))
            if execute:
                try:
                    db.commit()
                except IntegrityError as exc:
                    db.rollback()
                    raise HTTPException(409, "Concurrent contractor import conflict; retry preview") from exc
        return {"source": source, "total": len(planned), **totals,
                "applied": execute,
                "notice": "Missing source IDs are never deleted or disabled automatically."}

    @app.post("/api/contractors/import/preview")
    def preview_import(data: ImportBatch, staff=Depends(require_staff)):
        return _run_import(data, staff, False)

    @app.post("/api/contractors/import/apply")
    def apply_import(data: ImportBatch, staff=Depends(require_staff)):
        return _run_import(data, staff, True)

    @app.get("/api/contractors/sources")
    def list_sources(staff=Depends(require_staff)):
        require_owner_or_admin(staff)
        with Session(engine) as db:
            origin_rows = db.scalars(select(ContractorOrigin).where(
                ContractorOrigin.organization == staff.organization)).all()
        counts = Counter(o.source for o in origin_rows)
        configured = source_descriptions(staff.organization)
        joined = {s["source"]: {**s, "count": counts.get(s["source"], 0)}
                  for s in configured}
        for key, count in counts.items():
            if key not in joined:
                joined[key] = {"source": key,
                               "kind": "demo" if key == "demo" else "csv_or_api",
                               "ready": False, "count": count}
        return {"sources": [joined[k] for k in sorted(joined)],
                "notice": "HTTPS JSON APIs require per-company server-side configuration and token. CSV imports work immediately."}

    @app.post("/api/contractors/demo-seed")
    def seed_demo_contractors(staff=Depends(require_staff)):
        require_owner_or_admin(staff)
        import os
        if os.getenv("DCC_ENV") != "development":
            raise HTTPException(403, "Demo seeding is disabled outside private development mode")
        created, unchanged = 0, 0
        synthetic = demo_rows()
        with Session(engine) as db:
            refs = [ref_key(r["external_id"]) for r in synthetic]
            existing = {r.contractor_ref: r for r in db.scalars(select(Contractor).where(
                Contractor.organization == staff.organization,
                Contractor.contractor_ref.in_(refs))).all()}
            origins = origins_for(db, staff.organization, refs)
            for fixture in synthetic:
                ref = ref_key(fixture["external_id"])
                existing_row = existing.get(ref)
                if existing_row:
                    if not (ref in origins and origins[ref].is_demo):
                        raise HTTPException(409, f"Refusing to overwrite non-demo contractor {ref}")
                    unchanged += 1
                    continue
                db.add(Contractor(
                    organization=staff.organization, contractor_ref=ref,
                    external_id=fixture["external_id"], name=fixture["name"],
                    email=None, status=fixture["status"],
                    home_lat=fixture["home"]["lat"], home_lon=fixture["home"]["lon"],
                    service_radius_miles=fixture["service_radius_miles"]))
                db.add(ContractorOrigin(
                    organization=staff.organization, contractor_ref=ref,
                    source="demo", source_ref=fixture["external_id"],
                    home_address=fixture["home_address"], is_demo=True,
                    last_synced_at=now()))
                db.add(ContractorAudit(
                    organization=staff.organization, contractor_ref=ref,
                    action="demo", staff_email=staff.email))
                created += 1
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise HTTPException(409, "Concurrent demo seeding conflict; refresh roster") from exc
        return {"created": created, "already_present": unchanged,
                "states": 50, "demo_total": 100,
                "notice": "Fictional addresses are non-deliverable; coordinates are approximate city areas."}

    @app.post("/api/contractors/demo-clear")
    def clear_demo_contractors(staff=Depends(require_staff)):
        require_owner_or_admin(staff)
        import os
        if os.getenv("DCC_ENV") != "development":
            raise HTTPException(403, "Demo cleanup is disabled outside private development mode")
        with Session(engine) as db:
            origins = list(db.scalars(select(ContractorOrigin).where(
                ContractorOrigin.organization == staff.organization,
                ContractorOrigin.is_demo == True)).all())  # noqa: E712
            keys = [o.contractor_ref for o in origins]
            if keys:
                q = select(Contractor).where(
                    Contractor.organization == staff.organization,
                    Contractor.contractor_ref.in_(keys))
                for row in db.scalars(q).all():
                    db.delete(row)
                for origin in origins:
                    db.delete(origin)
                db.commit()
        return {"removed": len(keys), "notice": "Only explicitly demo-flagged roster records were removed."}

    @app.post("/api/contractors/sources/{source}/preview")
    def preview_remote_source(source: str, staff=Depends(require_staff)):
        require_owner_or_admin(staff)
        try:
            raw = fetch_contractors(staff.organization, source)
            if not raw:
                return {"source": source, "total": 0, "create": 0, "update": 0,
                        "unchanged": 0, "applied": False,
                        "notice": "Upstream returned zero records; no existing contractor was modified."}
            batch = ImportBatch(source=source, contractors=raw)
        except (ValueError, httpx.HTTPError) as exc:
            # Intentionally omit URL/token from user-facing error.
            raise HTTPException(502, f"Connector pull failed: {type(exc).__name__}") from exc
        return _run_import(batch, staff, False)

    @app.post("/api/contractors/sources/{source}/pull")
    def pull_remote_source(source: str, staff=Depends(require_staff)):
        require_owner_or_admin(staff)
        try:
            raw = fetch_contractors(staff.organization, source)
            if not raw:
                return {"source": source, "total": 0, "create": 0, "update": 0,
                        "unchanged": 0, "applied": False,
                        "notice": "Upstream returned zero records; no existing contractor was modified."}
            batch = ImportBatch(source=source, contractors=raw)
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(502, f"Connector pull failed: {type(exc).__name__}") from exc
        return _run_import(batch, staff, True)

    # Registered API routes remain in this module; the integration manager
    # reuses these exact audited import semantics and source-qualified IDs.
    return _run_import, ImportBatch
