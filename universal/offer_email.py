"""Contractor route email: branded HTML with plaintext fallback."""
import html
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlparse

def delivery_status():
    u = urlparse(os.getenv("DCC_PUBLIC_URL", ""))
    if u.scheme != "https" or not u.hostname or u.hostname.lower() in ("localhost", "127.0.0.1"):
        return False, "A public HTTPS site is needed before emailing contractors"
    if not os.getenv("DCC_SMTP_HOST") or not os.getenv("DCC_SMTP_FROM"):
        return False, "SMTP delivery is not configured"
    return True, ""

def render_email(organization, route, expiration, link, plan=None, preview=False, company_name=None, due_date=None, work_order_label=None):
    clean = lambda value: html.escape(str(value), quote=True)
    company = (company_name or organization.replace("-", " ").replace("_", " ").title()).strip()
    title = work_order_label.strip() if work_order_label else route.title
    price = "$" + "{:,.2f}".format(float(route.price))
    miles = plan.get("road_miles") if plan else None
    hours = plan.get("driving_hours") if plan else None
    subject = "Route invitation | " + re.sub(r"[\r\n]+", " ", title)[:140]
    summary = str(route.stops) + " stops · " + str(route.tasks) + " tasks · " + price
    if due_date is not None:
        summary += " · Due " + str(due_date)
    if miles is not None:
        summary += " · " + str(miles) + " road miles"
    if hours is not None:
        summary += " · " + str(hours) + " driving hours"
    expire_text = expiration.strftime("%B %d, %Y at %I:%M %p UTC")
    h = (
        '<!doctype html><html><head><meta charset="utf-8"></head>'
        '<body style="background:#f4f7fc;margin:0;padding:35px 12px;font-family:Arial,Helvetica,sans-serif;color:#243b5b">'
        '<table role="presentation" cellspacing="0" cellpadding="0" width="100%" style="max-width:600px;margin:auto;background:#fff;border:1px solid #e4eaf4;border-radius:16px">'
        '<tr><td style="background:#132a49;color:#fff;padding:26px 30px;font-size:17px;font-weight:bold">' + clean(company) + '<div style="font-size:11px;color:#b9d9fa;letter-spacing:2px;margin-top:7px">ROUTE INVITATION</div></td></tr>'
        '<tr><td style="padding:31px"><div style="color:#246ad2;font-size:12px;font-weight:bold">YOU HAVE A NEW ROUTE</div>'
        '<h1 style="font-size:27px;line-height:1.3;color:#172e4d">' + clean(title) + '</h1>'
        '<p style="line-height:1.7;color:#60748f;font-size:14px">Review the route details and available stops, then decide whether to accept, counter, or decline.</p>'
        '<div style="background:#f0f6ff;padding:18px;border-radius:11px;margin:23px 0;font-size:15px;font-weight:bold;color:#1a3455">' + clean(summary) + '</div>'
        + ('<p style="color:#916211;font-size:12px">DESIGN PREVIEW ONLY. No invitation has been sent.</p>' if preview else '')
        + '<p style="text-align:center;margin:34px 0"><a href="' + clean(link) + '" style="background:#2269d8;color:#fff;padding:18px 32px;border-radius:10px;font-size:15px;font-weight:bold;text-decoration:none">View route &amp; respond</a></p>'
        '<p style="font-size:12px;line-height:1.6;color:#688098">The route summary, available stop details, and response options are available through the button above.</p>'
        '<div style="font-size:12px;color:#59718f;background:#f4f8ff;padding:14px;border-radius:9px">Invitation expires: ' + clean(expire_text) + '</div></td></tr>'
        '<tr><td style="font-size:11px;line-height:1.6;color:#7c8da6;padding:23px 30px;background:#f9fbff;border-top:1px solid #e9edf5">Sent through DCC Universal for ' + clean(company) + '. This private link is intended for you only. No login is required. Do not forward this email.</td></tr>'
        '</table></body></html>'
    )
    plain = "Route invitation from " + company + "\n" + title + "\n" + summary + "\n\nView route and respond:\n" + link + "\n\nExpires " + expire_text
    return {"subject": subject, "html": h, "text": plain}

def send_invitation(to_address, rendered):
    ok, reason = delivery_status()
    if not ok:
        raise RuntimeError(reason)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to_address):
        raise ValueError("Invalid email recipient")
    msg = EmailMessage()
    msg["Subject"] = rendered["subject"]
    msg["From"] = os.environ["DCC_SMTP_FROM"]
    msg["To"] = to_address
    msg.set_content(rendered["text"])
    msg.add_alternative(rendered["html"], subtype="html")
    port = int(os.getenv("DCC_SMTP_PORT", "587"))
    if port not in (465, 587):
        raise RuntimeError("SMTP requires port 465 or 587")
    host = os.environ["DCC_SMTP_HOST"]
    conn = (smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
            if port == 465 else smtplib.SMTP(host, port, timeout=20))
    with conn:
        if port == 587:
            conn.starttls(context=ssl.create_default_context())
        if os.getenv("DCC_SMTP_USER"):
            conn.login(os.environ["DCC_SMTP_USER"], os.environ["DCC_SMTP_PASSWORD"])
        conn.send_message(msg)
