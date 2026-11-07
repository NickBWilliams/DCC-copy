"""Passwordless, single-route contractor offer links. No contractor accounts."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

def install(app, Base, Route, Event, engine, authenticate, serialize, record, database_url):
    class Offer(Base):
        __tablename__ = "universal_offers"
        id: Mapped[int] = mapped_column(primary_key=True)
        route_id: Mapped[int] = mapped_column(ForeignKey("universal_routes.id"), index=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
        expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
        revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    class OfferReply(BaseModel):
        action: Literal["accept", "decline", "counter"]
        amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)

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
    def issue_offer(route_id: int, x_organization: str = Header(), x_api_key: str | None = Header(default=None)):
        authenticate(x_organization, x_api_key)
        with Session(engine) as db:
            q = select(Route).where(Route.id == route_id, Route.organization == x_organization)
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
            offer = Offer(route_id=route.id, organization=x_organization, token_hash=hash_token(token), expires_at=utcnow()+timedelta(hours=48))
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
            return {"title": route.title, "stops": route.stops, "tasks": route.tasks, "price": str(route.price), "expires_at": offer.expires_at.isoformat()}

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
    def revoke_offers(route_id: int, x_organization: str = Header(), x_api_key: str | None = Header(default=None)):
        authenticate(x_organization, x_api_key)
        with Session(engine) as db:
            q = select(Route).where(Route.id == route_id, Route.organization == x_organization)
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
