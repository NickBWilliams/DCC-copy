import importlib
import re
import sys
from io import BytesIO
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from pypdf import PdfReader


@pytest.fixture
def app_client(tmp_path,monkeypatch,capsys):
    monkeypatch.setenv('DCC_ENV','development')
    monkeypatch.setenv('DCC_OWNER_EMAIL','owner@example.test')
    monkeypatch.setenv('DCC_ORGANIZATION_SLUG','alpha')
    monkeypatch.setenv('DCC_DATABASE_URL',f'sqlite:///{tmp_path}/receipt.db')
    monkeypatch.setenv('DCC_PUBLIC_URL','http://localhost:8000')
    module=importlib.reload(sys.modules['app']) if 'app' in sys.modules else importlib.import_module('app')
    with TestClient(module.app) as client:
        def login(address):
            client.post('/api/auth/request-link',json={'email':address})
            token=re.search(r'login_token=([A-Za-z0-9_-]+)',capsys.readouterr().out).group(1)
            assert client.post('/api/auth/redeem',json={'token':token}).status_code==200
        login('owner@example.test')
        yield client,module,login


def accept_example(client):
    created=client.post('/api/routes',json={'title':'Example route','contractor':'Worker',
        'stops':2,'tasks':2,'price':'125.50'}).json()
    rid=created['id']
    assert client.post(f'/api/routes/{rid}/transition',json={'action':'prepare','expected_version':created['version']}).status_code==200
    issue=client.post(f'/api/routes/{rid}/offers').json()
    token=issue['token']
    reply={'action':'accept','legal_name':'Example Worker','certified':True,'acknowledgment_version':1}
    response=client.post(f'/api/offers/{token}/respond',json=reply)
    assert response.status_code==200,response.text
    return rid,token,reply,response.json()


def test_receipt_survives_offer_expiry_and_is_read_only(app_client):
    client,module,_=app_client
    rid,token,reply,response=accept_example(client)
    assert response['accepted_path']==f'/accepted/{token}'
    assert client.post(f'/api/offers/{token}/respond',json=reply).status_code==410
    assert client.get(f'/api/offers/{token}').status_code==410
    viewed=client.get(f'/api/accepted/{token}')
    assert viewed.status_code==200 and viewed.json()['read_only'] is True
    assert viewed.json()['price']=='125.50'
    assert 'price_breakdown' not in viewed.json()
    assert client.post(f'/api/accepted/{token}/respond',json={'action':'decline'}).status_code==404
    with module.engine.begin() as db:
        db.execute(text("UPDATE universal_offers SET expires_at='2000-01-01'"))
    assert client.get(f'/accepted/{token}').status_code==200
    pdf=client.get(f'/api/accepted/{token}/document')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    extracted=''.join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)
    assert '125.50' in extracted and 'Example Worker' in extracted
    assert client.get(f'/api/routes/{rid}/acceptance/document').content==pdf.content
    state=client.get(f'/api/routes/{rid}/acceptance').json()
    assert state['email_status']=='blocked'
    assert client.post(f'/api/routes/{rid}/acceptance/revoke-access').status_code==200
    assert client.get(f'/api/accepted/{token}').status_code==410
    assert client.get(f'/api/accepted/{token}/document').status_code==410


def test_confirmation_sends_once_and_failure_keeps_acceptance(app_client,monkeypatch):
    client,module,_=app_client
    import route_receipt
    calls=[]
    monkeypatch.setattr(route_receipt,'delivery_status',lambda:(True,''))
    monkeypatch.setattr(route_receipt,'send_invitation',lambda recipient,rendered:calls.append((recipient,rendered)))
    assert client.post('/api/contractors',json={'external_id':'worker','name':'Worker','email':'worker@example.test',
        'home':{'lat':41,'lon':-88},'status':'active','service_radius_miles':100}).status_code==201
    rid,token,reply,_=accept_example(client)
    assert len(calls)==1 and calls[0][0]=='worker@example.test'
    assert '/accepted/'+token in calls[0][1]['text']
    assert calls[0][1]['attachments'][0][1].startswith(b'%PDF')
    assert client.get(f'/api/routes/{rid}/acceptance').json()['email_status']=='sent'
    assert client.post(f'/api/routes/{rid}/acceptance/retry-email').status_code==409
    assert client.post(f'/api/offers/{token}/respond',json=reply).status_code==410
    assert len(calls)==1
    def failing(*args):raise RuntimeError('SMTP unavailable')
    monkeypatch.setattr(route_receipt,'send_invitation',failing)
    rid,token,_,_=accept_example(client)
    assert client.get(f'/api/accepted/{token}').status_code==200
    assert client.get(f'/api/routes/{rid}/acceptance').json()['email_status']=='unknown'
    assert client.post(f'/api/routes/{rid}/acceptance/retry-email').status_code==409


def test_company_settings_and_receipts_cannot_cross_tenants(app_client):
    client,module,login=app_client
    settings=client.get('/api/planning-settings').json()
    settings['pricing']['per_mile']='0.75'
    updated=client.put('/api/planning-settings',json=settings)
    assert updated.status_code==200
    assert client.put('/api/planning-settings',json=settings).status_code==409
    rid,token,_,_=accept_example(client)
    with module.engine.begin() as db:
        db.execute(text("INSERT INTO dcc_staff_identities (email) VALUES ('other@example.test')"))
        db.execute(text("INSERT INTO dcc_staff_memberships (identity_id,organization,role) SELECT id,'bravo','owner' FROM dcc_staff_identities WHERE email='other@example.test'"))
    login('other@example.test')
    assert Decimal(client.get('/api/planning-settings').json()['pricing']['per_mile'])==0
    assert client.get(f'/api/routes/{rid}/acceptance/document').status_code==404
    assert client.get(f'/api/routes/{rid}/acceptance').status_code==404
    assert client.post(f'/api/routes/{rid}/acceptance/retry-email').status_code==404
    assert client.post(f'/api/routes/{rid}/acceptance/revoke-access').status_code==404


def test_daily_route_uses_company_defaults_and_freezes_accepted_snapshot(app_client,monkeypatch):
    client,module,_=app_client
    import dispatch
    def roads(home,stops):
        n=len(stops)+1
        return {'provider':'mock','durations':[[0 if i==j else 1800 for j in range(n)] for i in range(n)],
                'distances':[[0 if i==j else 16093.44 for j in range(n)] for i in range(n)]}
    monkeypatch.setattr(dispatch,'road_matrix',roads)
    defaults=client.get('/api/planning-settings').json()
    defaults['pricing'].update(per_mile='1',per_hour='10',hourly_basis='total_work')
    saved=client.put('/api/planning-settings',json=defaults).json()
    payload={'title':'Three working days','contractor':'Worker','home':{'lat':40,'lon':-80},
             'tasks':[{'external_task_id':str(i),'venue':'Example Store','address':f'{100+i} Example St',
                       'lat':40+i*.01,'lon':-80,'service_minutes':180} for i in range(6)]}
    created=client.post('/api/dispatch/routes',json=payload)
    assert created.status_code==201,created.text
    route=created.json()['route'];plan=created.json()['plan']
    assert plan['day_count']==3 and plan['price']=='330.00'
    token=client.post(f"/api/routes/{route['id']}/offers").json()['token']
    result=client.post(f'/api/offers/{token}/respond',json={'action':'accept','legal_name':'Worker Example','certified':True,'acknowledgment_version':1})
    assert result.status_code==200,result.text
    snapshot=client.get(f'/api/accepted/{token}').json()
    assert snapshot['day_count']==3 and snapshot['total_hours']==24
    assert sum(Decimal(d['price']) for d in snapshot['days'])==Decimal('330.00')
    assert len(snapshot['itinerary'])==6
    saved['pricing']['per_mile']='10'
    assert client.put('/api/planning-settings',json=saved).status_code==200
    assert client.get(f'/api/accepted/{token}').json()==snapshot
    pdf=client.get(snapshot['pdf_path']).content
    content=''.join(p.extract_text() for p in PdfReader(BytesIO(pdf)).pages)
    assert 'Day 3' in content and '$330.00' in content and '105 Example St' in content


def test_real_email_builder_attaches_receipt_without_network(app_client,monkeypatch):
    """Exercise MIME assembly and acceptance delivery; only SMTP transport is fake."""
    import offer_email
    client,module,_=app_client
    monkeypatch.setenv('DCC_PUBLIC_URL','https://dispatch.example.com')
    monkeypatch.setenv('DCC_SMTP_HOST','smtp.example.com')
    monkeypatch.setenv('DCC_SMTP_FROM','dispatch@example.com')
    monkeypatch.setenv('DCC_SMTP_PORT','587')
    messages=[]
    class Transport:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def starttls(self,**kwargs):pass
        def login(self,*args):pass
        def send_message(self,msg):messages.append(msg)
    monkeypatch.setattr(offer_email.smtplib,'SMTP',Transport)
    assert client.post('/api/contractors',json={'external_id':'worker','name':'Worker','email':'worker@example.test',
        'home':{'lat':41,'lon':-88},'status':'active','service_radius_miles':100}).status_code==201
    rid,token,_,_=accept_example(client)
    assert len(messages)==1
    msg=messages[0]
    assert msg['To']=='worker@example.test'
    assert 'https://dispatch.example.com/accepted/'+token in msg.get_body(preferencelist=('plain',)).get_content()
    files=list(msg.iter_attachments())
    assert len(files)==1 and files[0].get_content_type()=='application/pdf'
    assert files[0].get_payload(decode=True)==client.get(f'/api/routes/{rid}/acceptance/document').content
    assert client.get('/api/acceptance-readiness').json()['email_ready']
