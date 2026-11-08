from test_route_receipts import app_client
from sqlalchemy import text
from datetime import datetime, timezone, timedelta
import pytest


def route_example(client,monkeypatch):
    import dispatch
    monkeypatch.setattr(dispatch,'road_matrix',lambda home,stops:{'provider':'test','durations':[[0,600,900],[600,0,300],[900,300,0]],'distances':[[0,1000,2000],[1000,0,1000],[2000,1000,0]]})
    monkeypatch.setattr(dispatch,'ordered_geometry',lambda home,stops:{'type':'LineString','coordinates':[list(home),*[list(s) for s in stops],list(home)]})
    data={'title':'History route','contractor':'Worker','home':{'lat':40,'lon':-80},'tasks':[{'external_task_id':str(i),'venue':'Store '+str(i),'address':f'{i} Example St','lat':40+i*.01,'lon':-80,'service_minutes':30} for i in (1,2)]}
    response=client.post('/api/dispatch/routes',json=data)
    assert response.status_code==201,response.text
    rid=response.json()['route']['id']
    token=client.post(f'/api/routes/{rid}/offers').json()['token']
    accepted=client.post(f'/api/offers/{token}/respond',json={'action':'accept','legal_name':'Worker Example','certified':True,'acknowledgment_version':1})
    assert accepted.status_code==200,accepted.text
    return rid,token


def event(rid,task='1',eid='event1',status='completed',days=1):
    return {'route_id':rid,'external_task_id':task,'event_id':eid,'status':status,
        'occurred_at':(datetime.now(timezone.utc)-timedelta(days=days)).isoformat(),
        'notes':'Completed artwork installation','photo_urls':['https://example.com/proof.jpg']}


def test_execution_history_preserves_agreement(app_client,monkeypatch):
    c,module,_=app_client
    rid,token=route_example(c,monkeypatch)
    before=c.get(f'/api/accepted/{token}').json()
    pdf=c.get(f'/api/accepted/{token}/document').content
    assert c.get(f'/routes/{rid}/map').status_code==200
    assert c.get(f'/api/routes/{rid}/acceptance/view').json()['read_only']
    first=event(rid)
    assert c.post('/api/completions/import',json={'updates':[first]}).json()['applied']==1
    assert c.post('/api/completions/import',json={'updates':[first]}).json()['duplicate']==1
    second=event(rid,task='2',eid='event2')
    assert c.post('/api/completions/import',json={'updates':[second]}).json()['applied']==1
    d=c.get(f'/api/history/{rid}').json()
    assert d['route']['status']=='completed' and d['route']['done']==2
    assert d['tasks'][0]['notes']==first['notes'] and d['tasks'][0]['photo_urls']==first['photo_urls']
    assert c.get(f'/api/accepted/{token}').json()==before
    assert c.get(f'/api/accepted/{token}/document').content==pdf
    assert c.get('/api/history?q=1%20Example').json()['total']==1
    assert c.get('/api/history?status=completed&completed_from=2000-01-01').json()['total']==1
    assert c.get('/api/history?completed_to=2000-01-01').json()['total']==0
    assert c.post(f'/api/offers/{token}/respond',json={'action':'decline'}).status_code==410


def test_stale_conflicts_atomicity_and_reopening(app_client,monkeypatch):
    c,module,_=app_client;rid,_=route_example(c,monkeypatch)
    first=event(rid)
    c.post('/api/completions/import',json={'updates':[first]})
    conflict={**first,'notes':'different'}
    assert c.post('/api/completions/import',json={'updates':[conflict]}).status_code==409
    stale=event(rid,eid='old',status='pending',days=2)
    assert c.post('/api/completions/import',json={'updates':[stale]}).json()['stale']==1
    second=event(rid,task='2',eid='second')
    bad=event(rid,task='wrong',eid='bad')
    assert c.post('/api/completions/import',json={'updates':[second,bad]}).status_code==422
    assert c.get(f'/api/history/{rid}').json()['route']['done']==1
    c.post('/api/completions/import',json={'updates':[second]})
    c.post('/api/completions/import',json={'updates':[event(rid,eid='correction',status='failed',days=.5)]})
    d=c.get(f'/api/history/{rid}').json()
    assert d['route']['status']=='finalized' and d['route']['done']==1
    assert len(d['completion_history'])==4
    assert any(not e['applied'] for e in d['completion_history'])


def test_manual_version_and_payment(app_client,monkeypatch):
    c,module,_=app_client;rid,_=route_example(c,monkeypatch)
    version=c.get(f'/api/history/{rid}').json()['route']['version']
    assert c.put(f'/api/routes/{rid}/payment',json={'status':'paid','expected_version':version}).status_code==409
    update={**event(rid),'expected_version':version}
    assert c.post('/api/completions/task',json=update).status_code==200
    assert c.post('/api/completions/task',json={**event(rid,task='2',eid='second'),'expected_version':version}).status_code==409
    version=c.get(f'/api/history/{rid}').json()['route']['version']
    assert c.post('/api/completions/task',json={**event(rid,task='2',eid='second'),'expected_version':version}).status_code==200
    version=c.get(f'/api/history/{rid}').json()['route']['version']
    assert c.put(f'/api/routes/{rid}/payment',json={'status':'paid','expected_version':version,'reference':'Invoice 12'}).status_code==200
    assert c.get('/api/history?payment=paid').json()['total']==1
    d=c.get(f'/api/history/{rid}').json()
    assert any(e['action']=='payment_paid' for e in d['events'])
    assert d['payment']['reference']=='Invoice 12'


def test_completion_tenant_and_auth(app_client,monkeypatch):
    c,module,login=app_client;rid,_=route_example(c,monkeypatch)
    with module.engine.begin() as db:
        db.execute(text("INSERT INTO dcc_staff_identities (email) VALUES ('other@example.test')"))
        db.execute(text("INSERT INTO dcc_staff_memberships (identity_id,organization,role) SELECT id,'bravo','owner' FROM dcc_staff_identities WHERE email='other@example.test'"))
    login('other@example.test')
    assert c.get('/api/history').json()['total']==0
    for path in (f'/api/history/{rid}',f'/routes/{rid}/map',f'/api/routes/{rid}/acceptance/view'):
        assert c.get(path).status_code==404
    assert c.post('/api/completions/import',json={'updates':[event(rid)]}).status_code==404
    assert c.put(f'/api/routes/{rid}/payment',json={'status':'paid','expected_version':1}).status_code==404
    c.cookies.clear()
    assert c.get('/api/history').status_code==401
    assert c.post('/api/completions/import',json={'updates':[event(rid)]}).status_code==401


def test_feed_configuration_and_idempotent_sync(app_client,monkeypatch):
    from cryptography.fernet import Fernet
    import completion
    c,module,_=app_client;rid,_=route_example(c,monkeypatch)
    monkeypatch.setenv('DCC_CONNECTOR_ENCRYPTION_KEY',Fernet.generate_key().decode())
    data={'url':'https://example.com/completions','api_token':'private-test-token'}
    assert c.put('/api/completions/feed',json=data).status_code==200
    settings=c.get('/api/completions/feed')
    assert 'private-test-token' not in settings.text and settings.json()['configured']
    batch=completion.CompletionBatch(updates=[event(rid)])
    seen=[]
    monkeypatch.setattr(completion,'pull_feed',lambda url,token:seen.append((url,token)) or batch)
    assert c.post('/api/completions/feed/sync').json()['applied']==1
    assert c.post('/api/completions/feed/sync').json()['duplicate']==1
    assert seen[0]==(data['url'],data['api_token'])
    assert c.get('/api/completions/feed').json()['last_sync']


def test_no_bulk_completion_of_unfinished_tasks_or_unsafe_photos(app_client,monkeypatch):
    c,module,_=app_client;rid,_=route_example(c,monkeypatch)
    r=c.get(f'/api/history/{rid}').json()['route']
    c.post(f'/api/routes/{rid}/transition',json={'action':'finalize','expected_version':r['version']})
    r=c.get(f'/api/history/{rid}').json()['route']
    assert c.post(f'/api/routes/{rid}/transition',json={'action':'complete','expected_version':r['version']}).status_code==409
    for bad in ('javascript:alert(1)','http://example.com/photo','https://user:password@example.com/photo'):
        assert c.post('/api/completions/import',json={'updates':[{**event(rid),'photo_urls':[bad]}]}).status_code==422


def test_dispatcher_can_update_work_but_not_payments_or_feeds(app_client,monkeypatch):
    c,module,login=app_client;rid,_=route_example(c,monkeypatch)
    with module.engine.begin() as db:
        db.execute(text("INSERT INTO dcc_staff_identities (email) VALUES ('dispatcher@example.test')"))
        db.execute(text("INSERT INTO dcc_staff_memberships (identity_id,organization,role) SELECT id,'alpha','dispatcher' FROM dcc_staff_identities WHERE email='dispatcher@example.test'"))
    login('dispatcher@example.test')
    version=c.get(f'/api/history/{rid}').json()['route']['version']
    assert c.post('/api/completions/task',json={**event(rid),'expected_version':version}).status_code==200
    assert c.get('/api/completions/feed').status_code==403
    assert c.post('/api/completions/import',json={'updates':[event(rid)]}).status_code==403
    assert c.put(f'/api/routes/{rid}/payment',json={'status':'needs_review','expected_version':version}).status_code==403
