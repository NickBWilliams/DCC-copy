"""Company-scoped completion ledger. Accepted agreements are never overwritten."""
import hashlib
import json
from datetime import datetime, timezone, date
from typing import Literal
from urllib.parse import urlparse
from pathlib import Path

import httpx
from fastapi import Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationError
from sqlalchemy import String, Text, Integer, ForeignKey, UniqueConstraint, select, update, func, or_
from sqlalchemy.orm import Mapped, mapped_column, Session
from sqlalchemy.exc import IntegrityError
from integrations import _valid_url, _require_cipher
from contractor_sources import _trusted_url, _require_public_dns


def utcnow():
    return datetime.now(timezone.utc)


class CompletionUpdate(BaseModel):
    route_id: int = Field(gt=0)
    external_task_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    status: Literal['pending','in_progress','completed','failed','removed']
    occurred_at: datetime
    completed_at: datetime | None = None
    notes: str = Field(default='', max_length=4000)
    photo_urls: list[str] = Field(default_factory=list, max_length=30)

    @field_validator('occurred_at','completed_at')
    @classmethod
    def aware(cls, value):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError('Include a timezone in timestamps')
            value = value.astimezone(timezone.utc)
            if value > utcnow():
                raise ValueError('Future completion timestamps are not allowed')
        return value

    @field_validator('photo_urls')
    @classmethod
    def photos(cls, values):
        for value in values:
            parsed = urlparse(value)
            if len(value)>2048 or parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Photo links must be HTTPS URLs without embedded credentials')
        return values

    @model_validator(mode='after')
    def completion_date(self):
        if self.status == 'completed' and self.completed_at is None:
            self.completed_at = self.occurred_at
        if self.status != 'completed' and self.completed_at is not None:
            raise ValueError('Only completed tasks may have a completion date')
        if self.completed_at and self.completed_at > self.occurred_at:
            raise ValueError('Completion date cannot follow the update time')
        return self


class CompletionBatch(BaseModel):
    updates: list[CompletionUpdate] = Field(max_length=1000)


class ManualUpdate(CompletionUpdate):
    expected_version: int = Field(ge=1)


class PaymentUpdate(BaseModel):
    status: Literal['not_submitted','needs_review','submitted','paid']
    expected_version: int = Field(ge=1)
    reference: str = Field(default='', max_length=200)


class FeedSettings(BaseModel):
    url: str = Field(max_length=500)
    api_token: str | None = Field(default=None, min_length=8, max_length=4096)


def pull_feed(url, token):
    url = _valid_url(url)
    _, hostname = _trusted_url(url)
    _require_public_dns(hostname)
    with httpx.Client(follow_redirects=False, trust_env=False, timeout=20) as client:
        with client.stream('GET', url, headers={'Authorization':'Bearer '+token,'Accept':'application/json'}) as response:
            if response.is_redirect:
                raise ValueError('Redirects are disabled')
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data)>4*1024*1024:
                    raise ValueError('Feed exceeds 4 MB')
    return CompletionBatch.model_validate_json(data)


def install(app, Base, Route, Event, engine, require_staff, record, serialize):
    class TaskState(Base):
        __tablename__='dcc_completion_tasks'
        __table_args__=(UniqueConstraint('route_id','external_task_id'),)
        id: Mapped[int] = mapped_column(primary_key=True)
        route_id: Mapped[int] = mapped_column(ForeignKey('universal_routes.id'), index=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        external_task_id: Mapped[str] = mapped_column(String(128))
        status: Mapped[str] = mapped_column(String(30))
        occurred_at: Mapped[str] = mapped_column(String(40))
        completed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
        notes: Mapped[str] = mapped_column(Text, default='')
        photos_json: Mapped[str] = mapped_column(Text, default='[]')
        source: Mapped[str] = mapped_column(String(40))

    class Ledger(Base):
        __tablename__='dcc_completion_events'
        __table_args__=(UniqueConstraint('organization','source','event_id'),)
        id: Mapped[int] = mapped_column(primary_key=True)
        route_id: Mapped[int] = mapped_column(ForeignKey('universal_routes.id'), index=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        source: Mapped[str] = mapped_column(String(40))
        event_id: Mapped[str] = mapped_column(String(128))
        payload_json: Mapped[str] = mapped_column(Text)
        digest: Mapped[str] = mapped_column(String(64))
        applied: Mapped[bool] = mapped_column()
        received_at: Mapped[str] = mapped_column(String(40))
        actor: Mapped[str] = mapped_column(String(320))

    class Payment(Base):
        __tablename__='dcc_route_payments'
        route_id: Mapped[int] = mapped_column(ForeignKey('universal_routes.id'), primary_key=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        status: Mapped[str] = mapped_column(String(30), default='not_submitted')
        reference: Mapped[str] = mapped_column(String(200), default='')
        updated_at: Mapped[str] = mapped_column(String(40))

    class Feed(Base):
        __tablename__='dcc_completion_feeds'
        organization: Mapped[str] = mapped_column(String(80), primary_key=True)
        url: Mapped[str] = mapped_column(String(500))
        encrypted_token: Mapped[str] = mapped_column(Text)
        last_sync: Mapped[str | None] = mapped_column(String(40), nullable=True)
        last_result: Mapped[str | None] = mapped_column(Text, nullable=True)

    tasks = Base.metadata.tables['dcc_dispatch_route_tasks']
    receipts = Base.metadata.tables['dcc_route_receipts']

    def staff_route(db, rid, staff):
        route = db.scalar(select(Route).where(Route.id==rid, Route.organization==staff.organization).with_for_update())
        if route is None:
            raise HTTPException(404,'Route not found')
        return route

    def admin(staff):
        if staff.role not in ('owner','admin'):
            raise HTTPException(403,'Company owner or admin required')

    def task_details(db, route):
        states={s.external_task_id:s for s in db.scalars(select(TaskState).where(TaskState.route_id==route.id,TaskState.organization==route.organization))}
        result=[]
        for task in db.execute(select(tasks).where(tasks.c.route_id==route.id,tasks.c.organization==route.organization).order_by(tasks.c.stop_position,tasks.c.id)).mappings():
            state=states.get(task['external_task_id'])
            result.append({**{key:task[key] for key in ('external_task_id','stop_position','venue','address','task_type')},
                'status':state.status if state else ('completed' if task['completed_at'] else 'pending'),
                'completed_at':state.completed_at if state else (task['completed_at'].isoformat() if task['completed_at'] else None),
                'notes':state.notes if state else '', 'photo_urls':json.loads(state.photos_json) if state else [],
                'source':state.source if state else None,'updated_at':state.occurred_at if state else None})
        return result

    def bump(db, route):
        old=route.version
        changed=db.execute(update(Route).where(Route.id==route.id,Route.version==old).values(version=old+1).execution_options(synchronize_session=False))
        if changed.rowcount!=1:
            raise HTTPException(409,'Route changed during update; refresh and retry')
        route.version=old+1

    def apply_one(db, item, staff, source, expected=None):
        payload=item.model_dump(mode='json')
        payload.pop('expected_version',None)
        encoded=json.dumps(payload,sort_keys=True,separators=(',',':'))
        digest=hashlib.sha256(encoded.encode()).hexdigest()
        route=staff_route(db,item.route_id,staff)
        prior=db.scalar(select(Ledger).where(Ledger.organization==staff.organization,Ledger.source==source,Ledger.event_id==item.event_id))
        if prior:
            if prior.digest!=digest:
                raise HTTPException(409,'Event ID already used for different data')
            return 'duplicate'
        if expected is not None and route.version!=expected:
            raise HTTPException(409,'Route changed; refresh before saving')
        if route.status not in ('accepted','finalized','completed'):
            raise HTTPException(409,'Completion updates require an accepted route')
        task=db.execute(select(tasks).where(tasks.c.route_id==route.id,tasks.c.organization==staff.organization,tasks.c.external_task_id==item.external_task_id)).mappings().first()
        if not task:
            raise HTTPException(422,'Task ID does not belong to this route')
        state=db.scalar(select(TaskState).where(TaskState.route_id==route.id,TaskState.external_task_id==item.external_task_id))
        observed=item.occurred_at.isoformat()
        applied=not state or item.occurred_at>datetime.fromisoformat(state.occurred_at)
        db.add(Ledger(route_id=route.id,organization=staff.organization,source=source,event_id=item.event_id,
            payload_json=encoded,digest=digest,applied=applied,received_at=utcnow().isoformat(),actor=staff.email))
        if not applied:
            return 'stale'
        bump(db,route)
        if state is None:
            state=TaskState(route_id=route.id,organization=staff.organization,external_task_id=item.external_task_id)
            db.add(state)
        state.status=item.status;state.occurred_at=observed
        state.completed_at=item.completed_at.isoformat() if item.completed_at else None
        state.notes=item.notes;state.photos_json=json.dumps(item.photo_urls);state.source=source
        db.execute(update(tasks).where(tasks.c.id==task['id']).values(completed_at=item.completed_at))
        db.flush()
        current=task_details(db,route)
        route.done=sum(t['status']=='completed' for t in current)
        if current and route.done==len(current):
            route.status='completed'
        elif route.status=='completed' or item.status!='pending':
            route.status='finalized'
        record(db,route,'task_'+item.status,source+': '+item.external_task_id)
        if route.status!='completed':
            payment=db.get(Payment,route.id)
            if payment and payment.status in ('submitted','paid'):
                record(db,route,'payment_review_required','Work changed after payment '+payment.status)
        return 'applied'

    def apply_batch(batch, staff, source):
        counts={'applied':0,'duplicate':0,'stale':0}
        try:
            with Session(engine) as db:
                for item in sorted(batch.updates,key=lambda u:(u.route_id,u.occurred_at)):
                    counts[apply_one(db,item,staff,source)]+=1
                db.commit()
        except IntegrityError as exc:
            raise HTTPException(409,'Concurrent completion update; retry this batch') from exc
        return counts

    @app.post('/api/completions/import')
    def import_updates(batch:CompletionBatch,staff=Depends(require_staff)):
        admin(staff)
        return apply_batch(batch,staff,'import')

    @app.post('/api/completions/task')
    def manual_update(item:ManualUpdate,staff=Depends(require_staff)):
        try:
            with Session(engine) as db:
                result=apply_one(db,item,staff,'manual',item.expected_version)
                db.commit()
                return {'result':result}
        except IntegrityError as exc:
            raise HTTPException(409,'Concurrent task update; refresh and retry') from exc

    @app.put('/api/routes/{route_id}/payment')
    def payment_update(route_id:int,item:PaymentUpdate,staff=Depends(require_staff)):
        admin(staff)
        with Session(engine) as db:
            route=staff_route(db,route_id,staff)
            if route.version!=item.expected_version:
                raise HTTPException(409,'Route changed; refresh before saving')
            if route.status not in ('accepted','finalized','completed'):
                raise HTTPException(409,'Payment tracking requires an accepted route')
            if item.status in ('submitted','paid') and route.status!='completed':
                raise HTTPException(409,'Complete the work before submitting payment')
            bump(db,route)
            payment=db.get(Payment,route.id)
            if not payment:
                payment=Payment(route_id=route.id,organization=staff.organization)
                db.add(payment)
            before=payment.status or 'not_submitted'
            payment.status=item.status;payment.reference=item.reference;payment.updated_at=utcnow().isoformat()
            record(db,route,'payment_'+item.status,f'{staff.email}: {before} → {item.status}; {item.reference}')
            db.commit()
        return {'status':item.status,'notice':'Recorded only; no money was sent.'}

    @app.get('/api/history')
    def history(q:str=Query(default='',max_length=200),status:str='',payment:str='',
                completed_from:date|None=None,completed_to:date|None=None,
                offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100),staff=Depends(require_staff)):
        with Session(engine) as db:
            stmt=select(Route).where(Route.organization==staff.organization)
            if status:stmt=stmt.where(Route.status==status)
            if q:
                pattern='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
                matches=select(tasks.c.route_id).where(tasks.c.organization==staff.organization,or_(tasks.c.venue.ilike(pattern,escape='\\'),tasks.c.address.ilike(pattern,escape='\\'),tasks.c.external_task_id.ilike(pattern,escape='\\')))
                stmt=stmt.where(or_(Route.title.ilike(pattern,escape='\\'),Route.contractor.ilike(pattern,escape='\\'),Route.id.in_(matches),Route.id==int(q) if q.isdigit() else False))
            if payment:
                matching=select(Payment.route_id).where(Payment.organization==staff.organization,Payment.status==payment)
                stmt=stmt.where(or_(Route.id.in_(matching),~Route.id.in_(select(Payment.route_id)) if payment=='not_submitted' else False))
            # Route completion date = latest currently completed task timestamp.
            latest=select(TaskState.route_id).where(TaskState.organization==staff.organization).group_by(TaskState.route_id)
            if completed_from:latest=latest.having(func.max(TaskState.completed_at)>=completed_from.isoformat())
            if completed_to:latest=latest.having(func.substr(func.max(TaskState.completed_at),1,10)<=completed_to.isoformat())
            if completed_from or completed_to:stmt=stmt.where(Route.status=='completed',Route.id.in_(latest))
            total=db.scalar(select(func.count()).select_from(stmt.subquery()))
            rows=[]
            for route in db.scalars(stmt.order_by(Route.id.desc()).offset(offset).limit(limit)):
                pay=db.get(Payment,route.id)
                completed=db.scalar(select(func.max(TaskState.completed_at)).where(TaskState.route_id==route.id)) if route.status=='completed' else None
                rows.append({**serialize(route),'payment_status':pay.status if pay else 'not_submitted','completed_at':completed})
            return {'routes':rows,'total':total,'offset':offset,'limit':limit}

    @app.get('/api/history/{route_id}')
    def detail(route_id:int,staff=Depends(require_staff)):
        with Session(engine) as db:
            route=staff_route(db,route_id,staff)
            pay=db.get(Payment,route.id)
            receipt=db.execute(select(receipts).where(receipts.c.route_id==route.id,receipts.c.organization==staff.organization).order_by(receipts.c.id.desc())).mappings().first()
            events=[{'action':e.action,'detail':e.detail,'at':e.at.replace(tzinfo=timezone.utc).isoformat()} for e in db.scalars(select(Event).where(Event.route_id==route.id,Event.organization==staff.organization).order_by(Event.id.desc()))]
            ledger=[{'source':e.source,'actor':e.actor,'received_at':e.received_at,'applied':e.applied,'update':json.loads(e.payload_json)} for e in db.scalars(select(Ledger).where(Ledger.route_id==route.id,Ledger.organization==staff.organization).order_by(Ledger.id.desc()))]
            return {'route':serialize(route),'tasks':task_details(db,route),'events':events,'completion_history':ledger,
                'payment':{'status':pay.status,'reference':pay.reference,'updated_at':pay.updated_at} if pay else {'status':'not_submitted','reference':''},
                'acceptance':{'email_status':receipt['email_status'],'email_error':receipt['email_error'],'pdf_path':f'/api/routes/{route.id}/acceptance/document','map_path':f'/routes/{route.id}/map'} if receipt else None}

    @app.get('/api/completions/feed')
    def feed_settings(staff=Depends(require_staff)):
        admin(staff)
        with Session(engine) as db:
            row=db.get(Feed,staff.organization)
            return {'configured':bool(row),'url':row.url if row else '', 'last_sync':row.last_sync if row else None,'last_result':json.loads(row.last_result) if row and row.last_result else None}

    @app.put('/api/completions/feed')
    def save_feed(data:FeedSettings,staff=Depends(require_staff)):
        admin(staff);url=_valid_url(data.url);cipher=_require_cipher()
        if data.api_token and any(c in data.api_token for c in '\r\n\x00'):
            raise HTTPException(422,'Invalid API token')
        with Session(engine) as db:
            row=db.get(Feed,staff.organization)
            if not row:
                if not data.api_token:raise HTTPException(422,'API token required')
                row=Feed(organization=staff.organization);db.add(row)
            row.url=url
            if data.api_token:row.encrypted_token=cipher.encrypt(data.api_token.encode()).decode()
            db.commit()
        return {'configured':True}

    @app.post('/api/completions/feed/sync')
    def sync_feed(staff=Depends(require_staff)):
        admin(staff)
        with Session(engine) as db:
            row=db.get(Feed,staff.organization)
            if not row:raise HTTPException(409,'Configure this company completion feed first')
            url=row.url
            try:token=_require_cipher().decrypt(row.encrypted_token.encode()).decode()
            except Exception as exc:raise HTTPException(503,'Completion feed credential unavailable') from exc
        try:batch=pull_feed(url,token)
        except (httpx.HTTPError,ValueError,ValidationError) as exc:
            raise HTTPException(502,'Completion feed could not be read or did not match the required format; no updates applied') from exc
        result=apply_batch(batch,staff,'feed')
        with Session(engine) as db:
            row=db.get(Feed,staff.organization);row.last_sync=utcnow().isoformat();row.last_result=json.dumps(result);db.commit()
        return result

    @app.get('/history',response_class=HTMLResponse)
    def history_page():
        return Path(__file__).with_name('history.html').read_text()

    def can_complete(db, route):
        current=task_details(db,route)
        if current and any(t['status']!='completed' for t in current):
            raise HTTPException(409,'Update each task in Completion & history before completing this route')
    return can_complete
