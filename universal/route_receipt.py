"""Immutable accepted-route document and read-only access, scoped per company."""
import hashlib
import json
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone
from html import escape

from fastapi import Depends, HTTPException, BackgroundTasks
from fastapi.responses import Response, HTMLResponse
from sqlalchemy import String, Text, LargeBinary, Integer, ForeignKey, select, update
from sqlalchemy.orm import Mapped, mapped_column, Session

from offer_email import delivery_status, send_invitation


def receipt_pdf(snapshot):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font_root = Path(reportlab.__file__).parent / 'fonts'
    if 'ReceiptSans' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('ReceiptSans', str(font_root / 'Vera.ttf')))
        pdfmetrics.registerFont(TTFont('ReceiptSansBold', str(font_root / 'VeraBd.ttf')))
    out = BytesIO()
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = 'ReceiptSansBold' if style.name in ('Title','Heading1','Heading2','Heading3') else 'ReceiptSans'
    styles['BodyText'].fontSize = 9
    styles['BodyText'].leading = 12
    styles['BodyText'].spaceBefore = 0
    styles['BodyText'].spaceAfter = 0
    text = lambda value, style='BodyText': Paragraph(escape(str(value)), styles[style])
    doc = SimpleDocTemplate(out, pagesize=(612,792), rightMargin=36, leftMargin=36,
                            topMargin=36, bottomMargin=40, title='Accepted route confirmation')
    flow = [text(snapshot['company_name'], 'Title'), text('Accepted route confirmation', 'Heading2'),
            text(snapshot.get('work_order_label') or snapshot['title'], 'Heading2'),
            text('Contractor: '+snapshot['contractor']),
            text('Accepted by: '+snapshot['accepted_by']),
            text('Recorded: '+snapshot['accepted_at']),
            text('Agreed total: $'+snapshot['price']+' | '+str(snapshot['stops'])+' stops'),
            text('Due: '+str(snapshot.get('due_date') or 'Confirm with dispatch')),
            Spacer(1,12)]
    if snapshot.get('total_hours') is not None:
        flow.append(text(f"Estimated total time: {snapshot['total_hours']} hours | Round-trip road miles: {snapshot['road_miles']}"))
    link = escape(snapshot['view_url'], quote=True)
    flow.extend([Paragraph(f'<link href="{link}" color="#246ad2">Open the private route map and full details</link>', styles['BodyText']),
                 text('View only. Acceptance, counteroffer and decline are closed.'), Spacer(1,12)])
    days = snapshot.get('days') or [{'day':1, 'itinerary':snapshot.get('itinerary',[])}]
    for day_index, day in enumerate(days):
        if day_index:
            flow.append(PageBreak())
        flow.append(text('Day '+str(day['day']), 'Heading2'))
        if day.get('total_hours') is not None:
            flow.append(text(f"{day['total_hours']} total hours | {day['road_miles']} round-trip miles" +
                             (f" | Allocated pay: ${day['price']}" if 'price' in day else '')))
            flow.append(text(f"Driving {day['driving_hours']}h; onsite {day['service_hours']}h; breaks {day['break_hours']}h; waiting {day.get('waiting_hours',0)}h."))
        rows = [[text('Stop'),text('Location and work')]]
        for stop in day['itinerary']:
            tasks = ', '.join(t.get('task_type','Service') for t in stop.get('tasks',[]))
            rows.append([text(stop.get('day_position',stop.get('position',''))),
                         [text(stop['venue']),text(stop['address']),text(tasks)]])
        if len(rows)>1:
            table = Table(rows,colWidths=[38,502],repeatRows=1,splitInRow=1,hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf2ff')),
                                      ('VALIGN',(0,0),(-1,-1),'TOP'),
                                      ('BOTTOMPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),
                                      ('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#dbe4f1'))]))
            flow.append(table)
    if snapshot.get('acknowledgment_text'):
        flow.extend([Spacer(1,12),text('Acknowledgment', 'Heading2'),text(snapshot['acknowledgment_text'])])
    if snapshot.get('acceptance_notes'):
        flow.extend([text('Contractor notes','Heading2'),text(snapshot['acceptance_notes'])])
    def footer(canvas, document):
        canvas.saveState();canvas.setFont('ReceiptSans',8);canvas.setFillColor(colors.HexColor('#62748b'))
        canvas.drawString(36,22,'Private accepted-route record');canvas.drawRightString(576,22,f'Page {document.page}');canvas.restoreState()
    doc.build(flow,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()


def install(app, Base, Route, engine, require_staff, record, database_url, resolve_recipient):
    class Receipt(Base):
        __tablename__='dcc_route_receipts'
        id: Mapped[int] = mapped_column(primary_key=True)
        offer_id: Mapped[int] = mapped_column(ForeignKey('universal_offers.id'), unique=True)
        route_id: Mapped[int] = mapped_column(ForeignKey('universal_routes.id'), index=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        token_hash: Mapped[str] = mapped_column(String(64), unique=True)
        snapshot_json: Mapped[str] = mapped_column(Text)
        pdf: Mapped[bytes] = mapped_column(LargeBinary)
        recipient: Mapped[str | None] = mapped_column(String(320), nullable=True)
        email_status: Mapped[str] = mapped_column(String(30))
        email_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
        access_revoked: Mapped[bool] = mapped_column(default=False)
        attempts: Mapped[int] = mapped_column(Integer, default=0)

    def prepare(db, route, offer, token, snapshot, recipient):
        # Snapshot and document persist atomically with the acceptance.
        ready, reason = delivery_status()
        row=Receipt(offer_id=offer.id,route_id=route.id,organization=route.organization,
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    snapshot_json=json.dumps(snapshot),pdf=receipt_pdf(snapshot),recipient=recipient,
                    email_status='pending' if ready and recipient else 'blocked',
                    email_error=None if ready and recipient else (reason or 'No unique contractor email in the company roster'))
        db.add(row);db.flush()
        record(db,route,'acceptance_document_created')
        return row.id, row.email_status

    def deliver(receipt_id):
        # Claim once before SMTP. Ambiguous interrupted sends remain 'sending'
        # for manual investigation, rather than automatic duplicate emails.
        with Session(engine) as db:
            claimed = db.execute(update(Receipt).where(Receipt.id==receipt_id,
                Receipt.email_status.in_(['pending','failed','blocked'])).values(email_status='sending'))
            if claimed.rowcount != 1:
                return
            db.commit()
            row=db.get(Receipt,receipt_id)
            snapshot=json.loads(row.snapshot_json)
            recipient=row.recipient
            attachment=row.pdf
        ready, reason=delivery_status()
        status, error='blocked',reason
        if ready and not recipient:
            error='No unique contractor email in the company roster'
        elif ready:
            subject='Route accepted | '+snapshot['title'].replace('\r',' ').replace('\n',' ')[:140]
            content=(f"{snapshot['company_name']}\nYour route acceptance is confirmed.\n\n"
                     f"Route: {snapshot['title']}\nAgreed pay: ${snapshot['price']}\n"
                     f"Stops: {snapshot['stops']}\nDue: {snapshot.get('due_date') or 'Confirm with dispatch'}\n"
                     f"\nView the detailed itinerary and map (view only):\n{snapshot['view_url']}\n"
                     '\nYour accepted route document is attached. Keep this private link for your own use.')
            content += '\n\nDaily breakdown:\n' + '\n'.join(f"Day {d['day']}: {d.get('total_hours','—')} hours, {d.get('road_miles','—')} round-trip miles" for d in snapshot.get('days',[]))
            rendered={'subject':subject,'text':content,'html':'<p>'+escape(content).replace('\n','<br>')+'</p><p><a href="'+escape(snapshot['view_url'],quote=True)+'">View accepted route and map</a></p>',
                      'attachments':[(f"route-{snapshot['route_id']}-accepted.pdf",attachment)]}
            try:
                send_invitation(recipient,rendered)
                status,error='sent',None
            except Exception:
                # SMTP may have accepted the message before the connection
                # failed. Do not auto-retry an uncertain result.
                status,error='unknown','Delivery could not be confirmed. Check the mailbox before retrying.'
        with Session(engine) as db:
            row=db.get(Receipt,receipt_id)
            row.email_status=status
            row.email_error=error or None;row.attempts+=1
            route=db.get(Route,row.route_id)
            record(db,route,'acceptance_email_'+status)
            db.commit()

    def by_token(db, token):
        if not 30<=len(token)<=256:
            raise HTTPException(404,'Accepted route not found')
        row=db.scalar(select(Receipt).where(Receipt.token_hash==hashlib.sha256(token.encode()).hexdigest()))
        if not row:
            raise HTTPException(404,'Accepted route not found')
        route=db.get(Route,row.route_id)
        if row.access_revoked or not route or route.status not in ('accepted','finalized','completed'):
            raise HTTPException(410,'This route view is no longer available')
        return row

    def by_staff(db, route_id, staff):
        route=db.scalar(select(Route).where(Route.id==route_id,Route.organization==staff.organization))
        if not route:
            raise HTTPException(404,'Route not found')
        row=db.scalar(select(Receipt).where(Receipt.route_id==route_id,Receipt.organization==staff.organization).order_by(Receipt.id.desc()))
        if not row:
            raise HTTPException(404,'No accepted-route document recorded')
        return row

    def pdf_response(row):
        return Response(row.pdf,media_type='application/pdf',headers={
            'Content-Disposition':f'attachment; filename="route-{row.route_id}-accepted.pdf"',
            'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    @app.get('/accepted/{token}',response_class=HTMLResponse)
    def accepted_page(token:str):
        with Session(engine) as db:
            by_token(db,token)
        return HTMLResponse(Path(__file__).with_name('offer.html').read_text(),headers={
            'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Robots-Tag':'noindex, nofollow'})

    @app.get('/api/accepted/{token}')
    def accepted_data(token:str):
        with Session(engine) as db:
            row=by_token(db,token)
            data=json.loads(row.snapshot_json)
            # Never expose delivery recipient or internal pricing settings.
            data.update(read_only=True,pdf_path=f'/api/accepted/{token}/document')
            return data

    @app.get('/api/accepted/{token}/document')
    def accepted_document(token:str):
        with Session(engine) as db:
            return pdf_response(by_token(db,token))

    @app.get('/routes/{route_id}/map',response_class=HTMLResponse)
    def staff_map(route_id:int,staff=Depends(require_staff)):
        with Session(engine) as db:
            by_staff(db,route_id,staff)
        return HTMLResponse(Path(__file__).with_name('offer.html').read_text(),headers={'Cache-Control':'no-store'})

    @app.get('/api/routes/{route_id}/acceptance/view')
    def staff_map_data(route_id:int,staff=Depends(require_staff)):
        with Session(engine) as db:
            row=by_staff(db,route_id,staff)
            data=json.loads(row.snapshot_json)
            data.pop('view_url',None)
            data.update(read_only=True,pdf_path=f'/api/routes/{route_id}/acceptance/document')
            return data

    @app.get('/api/acceptance-readiness')
    def acceptance_readiness(staff=Depends(require_staff)):
        ready, reason=delivery_status()
        return {'email_ready':ready,'reason':reason}

    @app.get('/api/routes/{route_id}/acceptance')
    def receipt_status(route_id:int,staff=Depends(require_staff)):
        with Session(engine) as db:
            row=by_staff(db,route_id,staff)
            return {'email_status':row.email_status,'email_error':row.email_error,
                    'pdf_path':f'/api/routes/{route_id}/acceptance/document',
                    'access_revoked':row.access_revoked}

    @app.get('/api/routes/{route_id}/acceptance/document')
    def staff_document(route_id:int,staff=Depends(require_staff)):
        with Session(engine) as db:
            return pdf_response(by_staff(db,route_id,staff))

    @app.post('/api/routes/{route_id}/acceptance/retry-email')
    def retry_email(route_id:int,background_tasks:BackgroundTasks,staff=Depends(require_staff)):
        with Session(engine) as db:
            row=by_staff(db,route_id,staff)
            if row.email_status in ('sent','sending','unknown'):
                raise HTTPException(409,'Delivery already sent or uncertain; inspect delivery before retrying')
            if row.access_revoked:
                raise HTTPException(409,'Accepted route access has been revoked')
            if not row.recipient:
                row.recipient=resolve_recipient(staff.organization,db.get(Route,route_id))
                db.commit()
            background_tasks.add_task(deliver,row.id)
            return {'status':'queued'}

    @app.post('/api/routes/{route_id}/acceptance/revoke-access')
    def revoke_access(route_id:int,staff=Depends(require_staff)):
        with Session(engine) as db:
            row=by_staff(db,route_id,staff);row.access_revoked=True
            record(db,db.get(Route,route_id),'accepted_view_revoked');db.commit()
        return {'revoked':True}

    return prepare,deliver
