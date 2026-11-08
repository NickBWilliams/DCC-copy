"""Private passwordless staff access. Contractors never receive staff accounts."""
import hashlib
import os
import re
import secrets
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlencode, urlparse

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

COOKIE = "dcc_staff_session"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_email(value):
    value = value.strip().lower()
    if len(value) > 254 or not EMAIL_RE.fullmatch(value):
        raise HTTPException(422, "Valid email address required")
    return value


def send_link(email, token):
    public_url = os.getenv("DCC_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    link = public_url + "/?"+ urlencode({"login_token": token})
    if os.getenv("DCC_ENV") == "development" and not os.getenv("DCC_SMTP_HOST"):
        # Local-only testing: this must never be enabled on an exposed server.
        print(f"[DEV ONLY] DCC login link for {email}: {link}", flush=True)
        return
    if not os.getenv("DCC_SMTP_HOST"):
        raise RuntimeError("SMTP is required for staff login outside local development")
    if not public_url.startswith("https://"):
        raise RuntimeError("Production login links require HTTPS")
    msg = EmailMessage()
    msg["Subject"] = "Your DCC Universal sign-in link"
    msg["From"] = os.getenv("DCC_SMTP_FROM", os.getenv("DCC_SMTP_USER", ""))
    msg["To"] = email
    msg.set_content("Open this one-time link within 15 minutes:\n\n" + link + "\n")
    host = os.environ["DCC_SMTP_HOST"]
    port = int(os.getenv("DCC_SMTP_PORT", "587"))
    if port == 465:
        client = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(host, port, timeout=20)
    with client:
        if port != 465:
            client.starttls(context=ssl.create_default_context())
        if os.getenv("DCC_SMTP_USER"):
            client.login(os.environ["DCC_SMTP_USER"], os.environ["DCC_SMTP_PASSWORD"])
        client.send_message(msg)


@dataclass(frozen=True)
class StaffActor:
    user_id: int
    email: str
    organization: str
    role: str


def install(app, Base, engine):
    class Identity(Base):
        __tablename__ = "dcc_staff_identities"
        id: Mapped[int] = mapped_column(primary_key=True)
        email: Mapped[str] = mapped_column(String(254), unique=True, index=True)

    class Membership(Base):
        __tablename__ = "dcc_staff_memberships"
        __table_args__ = (UniqueConstraint("identity_id", "organization"),)
        id: Mapped[int] = mapped_column(primary_key=True)
        identity_id: Mapped[int] = mapped_column(ForeignKey("dcc_staff_identities.id"), index=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        role: Mapped[str] = mapped_column(String(20))

    class LoginLink(Base):
        __tablename__ = "dcc_staff_login_links"
        id: Mapped[int] = mapped_column(primary_key=True)
        identity_id: Mapped[int] = mapped_column(ForeignKey("dcc_staff_identities.id"))
        token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
        expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
        used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    class StaffSession(Base):
        __tablename__ = "dcc_staff_sessions"
        id: Mapped[int] = mapped_column(primary_key=True)
        identity_id: Mapped[int] = mapped_column(ForeignKey("dcc_staff_identities.id"), index=True)
        organization: Mapped[str] = mapped_column(String(80))
        token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
        expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
        revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    class EmailInput(BaseModel):
        email: str = Field(min_length=3, max_length=254)

    class TokenInput(BaseModel):
        token: str = Field(min_length=30, max_length=256)

    class InviteInput(EmailInput):
        role: str = Field(default="dispatcher", pattern="^(admin|manager|dispatcher)$")

    class OrganizationInput(BaseModel):
        organization: str = Field(min_length=1, max_length=80)

    def bootstrap():
        email = os.getenv("DCC_OWNER_EMAIL", "").strip()
        if not email:
            raise RuntimeError("DCC_OWNER_EMAIL must be configured")
        email = normalize_email(email)
        org = os.getenv("DCC_ORGANIZATION_SLUG", "personal").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", org):
            raise RuntimeError("Invalid DCC_ORGANIZATION_SLUG")
        with Session(engine) as db:
            user = db.scalar(select(Identity).where(Identity.email == email))
            if user is None:
                user = Identity(email=email)
                db.add(user)
                db.flush()
            membership = db.scalar(select(Membership).where(
                Membership.identity_id == user.id, Membership.organization == org))
            if membership is None:
                db.add(Membership(identity_id=user.id, organization=org, role="owner"))
            db.commit()

    def membership_list(db, identity_id):
        return list(db.scalars(select(Membership).where(
            Membership.identity_id == identity_id).order_by(Membership.organization)))

    def require_staff(request: Request):
        token = request.cookies.get(COOKIE, "")
        if len(token) < 30 or len(token) > 256:
            raise HTTPException(401, "Staff sign-in required")
        with Session(engine) as db:
            session = db.scalar(select(StaffSession).where(StaffSession.token_hash == digest(token)))
            if session is None or session.revoked_at is not None or aware(session.expires_at) <= now():
                raise HTTPException(401, "Session expired")
            member = db.scalar(select(Membership).where(
                Membership.identity_id == session.identity_id,
                Membership.organization == session.organization))
            if member is None:
                raise HTTPException(403, "No company membership")
            user = db.get(Identity, session.identity_id)
            if user is None:
                raise HTTPException(401, "Staff account unavailable")
            return StaffActor(user.id, user.email, member.organization, member.role)

    def profile(actor, db):
        return {"email": actor.email, "organization": actor.organization, "role": actor.role,
                "memberships": [{"organization": m.organization, "role": m.role}
                                for m in membership_list(db, actor.user_id)]}

    @app.post("/api/auth/request-link")
    def request_link(data: EmailInput):
        email = normalize_email(data.email)
        if os.getenv("DCC_ENV") != "development" and not os.getenv("DCC_SMTP_HOST"):
            raise HTTPException(503, "Staff email delivery not configured")
        with Session(engine) as db:
            user = db.scalar(select(Identity).where(Identity.email == email))
            if user is None:
                return {"message": "If this staff address is registered, a sign-in link will be sent."}
            latest = db.scalar(select(LoginLink).where(LoginLink.identity_id == user.id)
                               .order_by(LoginLink.id.desc()))
            if latest is not None and (now() - aware(latest.created_at)).total_seconds() < 60:
                return {"message": "If this staff address is registered, a sign-in link will be sent."}
            token = secrets.token_urlsafe(32)
            link = LoginLink(identity_id=user.id, token_hash=digest(token),
                             created_at=now(), expires_at=now() + timedelta(minutes=15))
            try:
                send_link(email, token)
            except Exception:
                # Never leak tokens or SMTP credentials in public errors.
                raise HTTPException(503, "Sign-in email could not be delivered")
            db.add(link)
            db.commit()
        return {"message": "If this staff address is registered, a sign-in link will be sent."}

    @app.post("/api/auth/redeem")
    def redeem(data: TokenInput, response: Response):
        with Session(engine) as db:
            query = select(LoginLink).where(LoginLink.token_hash == digest(data.token))
            if not str(engine.url).startswith("sqlite"):
                query = query.with_for_update()
            link = db.scalar(query)
            if link is None or link.used_at is not None or aware(link.expires_at) <= now():
                raise HTTPException(401, "Sign-in link invalid or expired")
            memberships = membership_list(db, link.identity_id)
            if not memberships:
                raise HTTPException(403, "No company membership")
            link.used_at = now()
            token = secrets.token_urlsafe(32)
            session = StaffSession(identity_id=link.identity_id,
                organization=memberships[0].organization, token_hash=digest(token),
                expires_at=now()+timedelta(days=7))
            db.add(session)
            db.commit()
            response.set_cookie(COOKIE, token, httponly=True,
                secure=os.getenv("DCC_ENV") != "development",
                samesite="lax", max_age=7*86400, path="/")
            user = db.get(Identity, link.identity_id)
            actor = StaffActor(user.id, user.email, memberships[0].organization, memberships[0].role)
            return profile(actor, db)

    @app.get("/api/auth/me")
    def me(actor: StaffActor = Depends(require_staff)):
        with Session(engine) as db:
            return profile(actor, db)

    @app.post("/api/auth/switch")
    def switch(data: OrganizationInput, request: Request,
               actor: StaffActor = Depends(require_staff)):
        with Session(engine) as db:
            member = db.scalar(select(Membership).where(
                Membership.identity_id == actor.user_id,
                Membership.organization == data.organization))
            if member is None:
                raise HTTPException(403, "Company membership required")
            session = db.scalar(select(StaffSession).where(
                StaffSession.token_hash == digest(request.cookies[COOKIE])))
            session.organization = member.organization
            db.commit()
            return profile(StaffActor(actor.user_id, actor.email, member.organization, member.role), db)

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response,
               actor: StaffActor = Depends(require_staff)):
        with Session(engine) as db:
            session = db.scalar(select(StaffSession).where(
                StaffSession.token_hash == digest(request.cookies[COOKIE])))
            if session is not None:
                session.revoked_at = now()
                db.commit()
        response.delete_cookie(COOKIE, path="/")
        return {"message": "Signed out"}

    @app.post("/api/staff", status_code=201)
    def invite(data: InviteInput, actor: StaffActor = Depends(require_staff)):
        if actor.role not in ("owner", "admin"):
            raise HTTPException(403, "Admin access required")
        email = normalize_email(data.email)
        with Session(engine) as db:
            user = db.scalar(select(Identity).where(Identity.email == email))
            if user is None:
                user = Identity(email=email)
                db.add(user)
                db.flush()
            existing = db.scalar(select(Membership).where(
                Membership.identity_id == user.id, Membership.organization == actor.organization))
            if existing:
                raise HTTPException(409, "Staff member already exists")
            db.add(Membership(identity_id=user.id, organization=actor.organization, role=data.role))
            db.commit()
            return {"email": email, "organization": actor.organization, "role": data.role,
                    "message": "Staff member added; they can request a sign-in link."}

    return bootstrap, require_staff
