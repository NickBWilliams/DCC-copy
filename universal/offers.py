"""Passwordless, single-route contractor offer links. No contractor accounts."""
import hashlib
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

def install(app, Base, Route, Event, engine, require_staff, serialize, record, database_url, dispatch_details=None):
    class Offer(Base):
        __tablename__ = "universal_offers"
        id: Mapped[int] = mapped_column(primary_key=True)
        route_id: Mapped[int] = mapped_column(ForeignKey("universal_routes.id"), index=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
        expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
        revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    DEFAULT_TERMS = (
        "I confirm I have reviewed all stops and tasks on this route, "
        "understand the work being offered, and agree to accept this route. "
        "My typed name records my acknowledgment."
    )

    class CompanyOfferSettings(Base):
        """Every organization owns its offer branding and confirmation terms."""
        __tablename__ = "dcc_company_offer_settings"
        organization: Mapped[str] = mapped_column(String(80), primary_key=True)
        display_name: Mapped[str] = mapped_column(String(90))
        acknowledgment_text: Mapped[str] = mapped_column(Text)
        acknowledgment_version: Mapped[int] = mapped_column(Integer, default=1)

    class OfferPresentation(Base):
        """Additive metadata table; keeps existing offer table unchanged."""
        __tablename__ = "dcc_offer_presentation"
        offer_id: Mapped[int] = mapped_column(ForeignKey("universal_offers.id"), primary_key=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
        work_order_label: Mapped[str | None] = mapped_column(String(120), nullable=True)

    class OfferDecision(Base):
        """Durable, tenant-scoped audit of one contractor response per link."""
        __tablename__ = "dcc_offer_decisions"
        __table_args__ = (UniqueConstraint("offer_id"),)
        id: Mapped[int] = mapped_column(primary_key=True)
        offer_id: Mapped[int] = mapped_column(ForeignKey("universal_offers.id"))
        organization: Mapped[str] = mapped_column(String(80), index=True)
        route_id: Mapped[int] = mapped_column(ForeignKey("universal_routes.id"))
        action: Mapped[str] = mapped_column(String(12))
        legal_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
        certified: Mapped[bool] = mapped_column(default=False)
        acknowledgment_text: Mapped[str | None] = mapped_column(Text, nullable=True)
        acknowledgment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
        notes: Mapped[str | None] = mapped_column(Text, nullable=True)
        counter_amount: Mapped[Decimal | None] = mapped_column(nullable=True)
        signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    class OfferReply(BaseModel):
        action: Literal["accept", "decline", "counter"]
        amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
        legal_name: str | None = Field(default=None, max_length=120)
        certified: bool = False
        notes: str | None = Field(default=None, max_length=500)

    class OfferIssueInput(BaseModel):
        due_date: date | None = None
        work_order_label: str | None = Field(default=None, max_length=120)

    class OfferSettingsInput(BaseModel):
        display_name: str = Field(min_length=2, max_length=90)
        acknowledgment_text: str = Field(min_length=30, max_length=1000)

    def company_brand(db, organization):
        setting = db.get(CompanyOfferSettings, organization)
        if setting:
            return {
                "display_name": setting.display_name,
                "acknowledgment_text": setting.acknowledgment_text,
                "acknowledgment_version": setting.acknowledgment_version,
            }
        return {"display_name": "DCC Universal",
                "acknowledgment_text": DEFAULT_TERMS,
                "acknowledgment_version": 1}

    @app.get("/api/offer-settings")
    def read_company_offer_settings(staff=Depends(require_staff)):
        with Session(engine) as db:
            return company_brand(db, staff.organization)

    @app.put("/api/offer-settings")
    def update_company_offer_settings(data: OfferSettingsInput, staff=Depends(require_staff)):
        if staff.role not in ("owner", "admin"):
            raise HTTPException(403, "Company owner/admin access required")
        brand = data.display_name.strip()
        terms = data.acknowledgment_text.strip()
        if len(brand) < 2 or len(terms) < 30:
            raise HTTPException(422, "A company name and confirmation text are required")
        with Session(engine) as db:
            setting = db.get(CompanyOfferSettings, staff.organization)
            if setting is None:
                setting = CompanyOfferSettings(organization=staff.organization,
                    display_name=brand, acknowledgment_text=terms, acknowledgment_version=1)
                db.add(setting)
            else:
                if setting.acknowledgment_text != terms:
                    setting.acknowledgment_version += 1
                setting.display_name = brand
                setting.acknowledgment_text = terms
            db.commit()
            return company_brand(db, staff.organization)

    def hash_token(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def utcnow():
        return datetime.now(timezone.utc)

    def get_offer(db, token, lock=False):
        if len(token) > 256 or len(token) < 30:
            raise HTTPException(404, "Offer not found")
        query = select(Offer).where(Offer.token_hash == hash_token(token))
        if lock and not database_url.startswith("sqlite"):
            query = query.with_for_update()
        offer = db.scalar(query)
        if offer is None:
            raise HTTPException(404, "Offer not found")
        expires = offer.expires_at.replace(tzinfo=timezone.utc) if offer.expires_at.tzinfo is None else offer.expires_at
        if offer.revoked_at is not None or offer.used_at is not None or expires <= utcnow():
            raise HTTPException(410, "This offer link is no longer active")
        return offer

    @app.post("/api/routes/{route_id}/offers", status_code=201)
    def issue_offer(route_id: int, staff=Depends(require_staff)):
        with Session(engine) as db:
            q = select(Route).where(Route.id == route_id, Route.organization == staff.organization)
            if not database_url.startswith("sqlite"):
                q = q.with_for_update()
            route = db.scalar(q)
            if route is None:
                raise HTTPException(404, "Route not found")
            if route.status not in ("ready", "flagged", "sent"):
                raise HTTPException(409, "Route must be ready, flagged, or sent")
            for old in db.scalars(select(Offer).where(Offer.route_id == route_id, Offer.revoked_at.is_(None), Offer.used_at.is_(None))):
                old.revoked_at = utcnow()
            token = secrets.token_urlsafe(32)
            offer = Offer(route_id=route.id, organization=staff.organization, token_hash=hash_token(token), expires_at=utcnow()+timedelta(hours=48))
            db.add(offer)
            route.status = "sent"
            route.version += 1
            record(db, route, "offer_issued")
            db.commit()
            return {"token": token, "offer_path": "/offer/" + token, "expires_at": offer.expires_at.isoformat(), "route": serialize(route)}

    @app.get("/api/offers/{token}")
    def view_offer(token: str):
        with Session(engine) as db:
            offer = get_offer(db, token)
            route = db.get(Route, offer.route_id)
            if route is None or route.status != "sent":
                raise HTTPException(410, "This offer has been withdrawn")
            body = {"title": route.title, "stops": route.stops, "tasks": route.tasks,
                    "price": str(route.price), "expires_at": offer.expires_at.isoformat()}
            if dispatch_details is not None:
                planned = dispatch_details(db, route, public=True)
                if planned is not None:
                    body.update(planned)
            return body

    @app.post("/api/offers/{token}/respond")
    def respond(token: str, reply: OfferReply):
        if reply.action == "counter" and (reply.amount is None or reply.amount <= 0):
            raise HTTPException(422, "A positive counteroffer amount is required")
        with Session(engine) as db:
            offer = get_offer(db, token, lock=True)
            q = select(Route).where(Route.id == offer.route_id, Route.organization == offer.organization)
            if not database_url.startswith("sqlite"):
                q = q.with_for_update()
            route = db.scalar(q)
            if route is None or route.status != "sent":
                raise HTTPException(410, "This offer has been withdrawn")
            route.status = {"accept":"accepted","decline":"declined","counter":"countered"}[reply.action]
            if reply.action == "counter":
                route.proposed_price = reply.amount
            route.version += 1
            offer.used_at = utcnow()
            record(db, route, "contractor_" + reply.action, "counter_amount=" + str(reply.amount) if reply.action == "counter" else "")
            db.commit()
            return {"status": route.status, "message": "Response recorded"}

    @app.post("/api/routes/{route_id}/offers/revoke")
    def revoke_offers(route_id: int, staff=Depends(require_staff)):
        with Session(engine) as db:
            q = select(Route).where(Route.id == route_id, Route.organization == staff.organization)
            if not database_url.startswith("sqlite"):
                q = q.with_for_update()
            route = db.scalar(q)
            if route is None:
                raise HTTPException(404, "Route not found")
            if route.status != "sent":
                raise HTTPException(409, "Only sent offers can be revoked")
            for offer in db.scalars(select(Offer).where(Offer.route_id == route_id, Offer.revoked_at.is_(None), Offer.used_at.is_(None))):
                offer.revoked_at = utcnow()
            route.status = "ready"
            route.version += 1
            record(db, route, "offers_revoked")
            db.commit()
            return serialize(route)

    from fastapi.responses import HTMLResponse
    from pathlib import Path

    @app.get("/offer/{token}", response_class=HTMLResponse)
    def offer_page(token: str):
        return Path(__file__).with_name("offer.html").read_text(encoding="utf-8")
