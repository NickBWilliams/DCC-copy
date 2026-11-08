"""Private, multi-company contractor directory and low-cost proximity matching.

No contractor accounts, logins or portals are created. Straight-line distance
is used ONLY to shortlist candidates; quote and pay use road-network routing.
"""
import math
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, select
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

    def serialize(row):
        return {
            "external_id":row.external_id,"name":row.name,"email":row.email,
            "status":row.status,
            "home":{"lon":row.home_lon,"lat":row.home_lat},
            "service_radius_miles":row.service_radius_miles,
            "version":row.version,
        }

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
            return [serialize(row) for row in rows]

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
        ranked=[]
        for row in rows:
            distance=miles_between(lon,lat,row.home_lon,row.home_lat)
            inside=distance <= row.service_radius_miles
            if inside or include_outside_radius:
                ranked.append({**serialize(row),"aerial_miles":round(distance,2),
                               "within_service_radius":inside})
        ranked.sort(key=lambda r:(not r["within_service_radius"],
                                   r["aerial_miles"],r["name"].casefold()))
        return {"distance_type":"aerial_shortlist_only",
                "notice":"Not road miles. Final route quotes use real road-network routing.",
                "candidates":ranked[:limit]}
