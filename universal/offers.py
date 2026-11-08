"""Passwordless, single-route contractor offer links. No contractor accounts."""
import hashlib
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from offer_email import delivery_status, render_email, send_invitation

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

def install(app, Base, Route, Event, engine, require_staff, serialize, record, database_url, dispatch_details=None, roster_candidates=None):
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
        acknowledgment_version: int | None = Field(default=None, ge=1)
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
    def issue_offer(route_id: int, data: OfferIssueInput | None = None, staff=Depends(require_staff)):
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
            db.flush()
            if data is not None:
                if data.due_date is not None and data.due_date < utcnow().date():
                    raise HTTPException(422, "Due date must be today or later")
                if data.work_order_label is not None and not data.work_order_label.strip():
                    raise HTTPException(422, "Work order label cannot be blank")
                db.add(OfferPresentation(
                    offer_id=offer.id, organization=staff.organization,
                    due_date=data.due_date,
                    work_order_label=data.work_order_label.strip() if data.work_order_label else None,
                ))
            route.status = "sent"
            route.version += 1
            record(db, route, "offer_issued")
            db.commit()
            brand = company_brand(db, staff.organization)["display_name"]
            order = (data.work_order_label if data and data.work_order_label else route.title)
            due = data.due_date.isoformat() if data and data.due_date else None
            public_url = os.getenv("DCC_PUBLIC_URL", "http://localhost:8000").rstrip("/")
            link = public_url + "/offer/" + token
            # COPYABLE template only: private preview has no contractor mail sender.
            message = (
                f"Hello,\n\n{brand} has a route available for you.\n\n"
                f"Route: {order}\nStops: {route.stops}\nTasks: {route.tasks}\n"
                f"Pay: $" + f"{route.price}\n"
                + (f"Due: {due}\n" if due else "")
                + f"\nReview and respond:\n{link}\n\n"
                "You may accept, counter, or decline. This link expires in 48 hours.\n"
            )
            plan = dispatch_details(db, route, public=True) if dispatch_details else None
            composed = render_email(
                organization=staff.organization, route=route,
                expiration=offer.expires_at, link=link, plan=plan,
                company_name=brand, due_date=due)
            return {
                "token": token, "offer_path": "/offer/" + token,
                "expires_at": offer.expires_at.isoformat(), "route": serialize(route),
                "delivery": "manual_only",
                "email_preview": {
                    "subject": composed["subject"], "body": composed["text"],
                    "html": composed["html"],
                },
            }

    @app.get("/api/offers/{token}")
    def view_offer(token: str):
        with Session(engine) as db:
            offer = get_offer(db, token)
            route = db.get(Route, offer.route_id)
            if route is None or route.status != "sent":
                raise HTTPException(410, "This offer has been withdrawn")
            brand = company_brand(db, offer.organization)
            presentation = db.get(OfferPresentation, offer.id)
            body = {"title": route.title, "stops": route.stops, "tasks": route.tasks,
                    "price": str(route.price), "expires_at": offer.expires_at.isoformat(),
                    "company_name": brand["display_name"],
                    "acknowledgment_text": brand["acknowledgment_text"],
                    "acknowledgment_version": brand["acknowledgment_version"],
                    "due_date": presentation.due_date.isoformat()
                        if presentation and presentation.due_date else None,
                    "work_order_label": presentation.work_order_label
                        if presentation else None}
            if dispatch_details is not None:
                planned = dispatch_details(db, route, public=True)
                if planned is not None:
                    body.update(planned)
            # A customer-facing map tile URL must be public, never a private
            # routing service token. OSM public tiles are preview-only.
            tiles = os.getenv("DCC_MAP_TILES_URL", "").strip()
            if not tiles and os.getenv("DCC_ENV") == "development":
                tiles = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            if tiles.startswith("https://"):
                body["map_tiles"] = {
                    "url": tiles,
                    "attribution": os.getenv("DCC_MAP_TILES_ATTRIBUTION", "© OpenStreetMap contributors"),
                }
            return body

    @app.post("/api/offers/{token}/respond")
    def respond(token: str, reply: OfferReply):
        if reply.action == "counter" and (reply.amount is None or reply.amount <= 0):
            raise HTTPException(422, "A positive counteroffer amount is required")
        if reply.action == "accept":
            if not reply.legal_name or len(reply.legal_name.strip()) < 2 or not reply.certified:
                raise HTTPException(422, "Acceptance requires a full typed name and certification")
            if reply.amount is not None:
                raise HTTPException(422, "Accepting a route cannot include a counteroffer amount")
        if reply.notes is not None and len(reply.notes.strip()) > 500:
            raise HTTPException(422, "Notes exceed 500 characters")
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
            brand = company_brand(db, offer.organization)
            if reply.action == "accept" and reply.acknowledgment_version != brand["acknowledgment_version"]:
                raise HTTPException(409, "Acceptance terms have changed; reload offer to review the latest text")
            response_at = utcnow()
            db.add(OfferDecision(
                offer_id=offer.id, organization=offer.organization,
                route_id=route.id, action=reply.action,
                legal_name=reply.legal_name.strip() if reply.action == "accept" else None,
                certified=reply.certified if reply.action == "accept" else False,
                acknowledgment_text=brand["acknowledgment_text"] if reply.action == "accept" else None,
                acknowledgment_version=brand["acknowledgment_version"] if reply.action == "accept" else None,
                notes=reply.notes.strip() if reply.notes else None,
                counter_amount=reply.amount if reply.action == "counter" else None,
                signed_at=response_at,
            ))
            record(db, route, "contractor_" + reply.action,
                   "counter_amount=" + str(reply.amount) if reply.action == "counter" else "")
            db.commit()
            return {"status": route.status, "message": "Response recorded",
                    "recorded_at": response_at.isoformat(),
                    "accepted_by": reply.legal_name.strip() if reply.action == "accept" else None}

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


    def _recipient_for_route(organization, route):
        """Never guess which worker to email if two IDs share one display name."""
        if roster_candidates is None:
            return None
        matches = [
            worker for worker in roster_candidates(
                organization, include_training=True, include_demo=False)
            if worker["name"].strip().casefold() == route.contractor.strip().casefold()
            and worker.get("email") and not worker.get("is_demo")
        ]
        return matches[0]["email"] if len(matches) == 1 else None

    @app.get("/api/routes/{route_id}/offers/email-preview")
    def preview_email(route_id: int, staff=Depends(require_staff)):
        """Show the customer-branded email without creating a usable offer."""
        with Session(engine) as db:
            route = db.scalar(select(Route).where(
                Route.id == route_id, Route.organization == staff.organization))
            if route is None:
                raise HTTPException(404, "Route not found")
            if route.status not in ("ready", "flagged", "sent"):
                raise HTTPException(409, "This route cannot currently be offered")
            brand = company_brand(db, staff.organization)["display_name"]
            plan = dispatch_details(db, route, public=True) if dispatch_details else None
            rendered = render_email(
                organization=staff.organization, company_name=brand,
                route=route, expiration=utcnow() + timedelta(hours=48),
                link="https://preview.invalid/offer/NOT-A-REAL-LINK",
                plan=plan, preview=True)
            deliverable, reason = delivery_status()
            recipient = _recipient_for_route(staff.organization, route)
            if not recipient:
                deliverable = False
                reason = "Choose one uniquely named, eligible, non-demo contractor with an email in this company roster"
            return {**rendered, "can_send": deliverable,
                    "recipient": recipient, "delivery_blocker": reason if not deliverable else None,
                    "notice": "Design preview only; no offer created and no email sent."}

    @app.post("/api/routes/{route_id}/offers/send-email", status_code=201)
    def send_offer_email(route_id: int, data: OfferIssueInput | None = None,
                         staff=Depends(require_staff)):
        """Send only after explicit staff action, using this tenant's contractor email.

        A production outbox queue is still needed for guaranteed delivery.
        No outgoing mail occurs in the localhost-only private preview.
        """
        deliverable, reason = delivery_status()
        if not deliverable:
            raise HTTPException(503, reason)
        with Session(engine) as db:
            query = select(Route).where(
                Route.id == route_id, Route.organization == staff.organization)
            if not database_url.startswith("sqlite"):
                query = query.with_for_update()
            route = db.scalar(query)
            if route is None:
                raise HTTPException(404, "Route not found")
            if route.status not in ("ready", "flagged", "sent"):
                raise HTTPException(409, "This route cannot currently be offered")
            recipient = _recipient_for_route(staff.organization, route)
            if not recipient:
                raise HTTPException(422, "Select one uniquely named, eligible contractor with an email in this company's roster")
            if data and data.due_date and data.due_date < utcnow().date():
                raise HTTPException(422, "Due date cannot be in the past")
            token = secrets.token_urlsafe(32)
            expiry = utcnow() + timedelta(hours=48)
            offer = Offer(route_id=route.id, organization=staff.organization,
                          token_hash=hash_token(token), expires_at=expiry)
            public_url = os.environ["DCC_PUBLIC_URL"].rstrip("/")
            brand = company_brand(db, staff.organization)["display_name"]
            plan = dispatch_details(db, route, public=True) if dispatch_details else None
            rendered = render_email(
                organization=staff.organization, company_name=brand,
                route=route, expiration=expiry,
                link=public_url + "/offer/" + token, plan=plan,
                due_date=data.due_date.isoformat() if data and data.due_date else None)
            try:
                # Do not revoke a prior offer or update a route on SMTP failure.
                send_invitation(recipient, rendered)
                for prior in db.scalars(select(Offer).where(
                    Offer.route_id == route.id,
                    Offer.revoked_at.is_(None), Offer.used_at.is_(None))):
                    prior.revoked_at = utcnow()
                db.add(offer)
                db.flush()
                if data and (data.due_date or data.work_order_label):
                    db.add(OfferPresentation(
                        offer_id=offer.id, organization=staff.organization,
                        due_date=data.due_date,
                        work_order_label=data.work_order_label.strip() if data.work_order_label else None))
                route.status = "sent"
                route.version += 1
                record(db, route, "offer_email_sent")
                db.commit()
            except Exception as exc:
                db.rollback()
                # Never include raw SMTP response, token or private email in an error.
                raise HTTPException(503, "Invitation could not be delivered; no route changes were saved") from exc
            return {"sent": True, "recipient": recipient,
                    "expires_at": expiry.isoformat(), "route": serialize(route),
                    "notice": "HTML route invitation sent with a private map link and plaintext fallback."}

    from fastapi.responses import HTMLResponse
    from pathlib import Path

    @app.get("/offer/{token}", response_class=HTMLResponse)
    def offer_page(token: str):
        return Path(__file__).with_name("offer.html").read_text(encoding="utf-8")
