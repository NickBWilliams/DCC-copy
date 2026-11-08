import math
import pytest
from decimal import Decimal
from day_planning import WorkdayPolicy, build_days
from routing import RoutingUnavailable
import routing
import dispatch


def matrix(n, seconds=1800):
    return {'durations':[[0 if i==j else seconds for j in range(n)] for i in range(n)],
            'distances':[[0 if i==j else 16093.44 for j in range(n)] for i in range(n)],
            'provider':'mock-road'}


def test_daily_loops_include_repeated_commutes_and_all_work():
    days=build_days([{'service_minutes':180} for _ in range(6)],matrix(7),WorkdayPolicy())
    assert len(days)==3
    assert sorted(i for d in days for i in d['stop_order'])==list(range(6))
    assert all(d['total_hours']==8 for d in days)
    assert sum(d['driving_hours'] for d in days)==4.5  # 9 legs, not 7
    assert sum(d['road_miles'] for d in days)==90


def test_one_oversize_stop_is_blocked_and_target_exception_is_flagged():
    assert build_days([{'service_minutes':480}],matrix(2),WorkdayPolicy())[0]['above_target']
    with pytest.raises(RoutingUnavailable,match='cannot fit'):
        build_days([{'service_minutes':540}],matrix(2),WorkdayPolicy())


def test_waiting_and_close_windows_count_toward_day():
    stop={'service_minutes':60,'window_start_minute':600,'window_end_minute':660}
    day=build_days([stop],matrix(2),WorkdayPolicy())[0]
    assert day['waiting_hours']==1
    assert day['total_hours']==3.5
    stop['window_end_minute']=650
    with pytest.raises(RoutingUnavailable):build_days([stop],matrix(2),WorkdayPolicy())


@pytest.mark.parametrize('value',[None,float('nan'),float('inf'),-1,True])
def test_bad_road_data_never_produces_pay(value):
    data=matrix(2);data['durations'][0][1]=value
    with pytest.raises(RoutingUnavailable):build_days([{'service_minutes':30}],data,WorkdayPolicy())


def test_company_daily_minimum_and_override_sum(monkeypatch):
    monkeypatch.setattr(dispatch,'road_matrix',lambda h,s:matrix(len(s)+1))
    payload={'title':'Daily example','contractor':'Example','home':{'lat':40,'lon':-80},
             'tasks':[{'external_task_id':str(i),'venue':'Example','address':f'{i} Example Road',
                       'lat':40+i*.01,'lon':-80,'service_minutes':180} for i in range(6)],
             'pricing':{'minimum':'100','per_mile':'1','per_hour':'10','per_service_hour':'5'}}
    plan=dispatch.plan(dispatch.PlanInput(**payload))
    assert plan['price']=='300.00'
    assert len(plan['days'])==3
    payload['offer_total']='250.01'
    plan=dispatch.plan(dispatch.PlanInput(**payload))
    assert sum(Decimal(d['price']) for d in plan['days'])==Decimal('250.01')
    assert plan['price_breakdown']['calculated_total']=='300.00'


def test_matrix_batches_preserve_asymmetric_costs(monkeypatch):
    monkeypatch.setattr(routing,'active_provider',lambda:('heigit','fake'))
    points=[(-80+i*.01,40) for i in range(55)]
    calls=[]
    def request(url,*,payload,headers):
        assert len(payload['locations'])<=50
        calls.append(payload)
        ids=[round((p[0]+80)*100) for p in payload['locations']]
        costs=[[0 if ids[a]==ids[b] else ids[a]*100+ids[b]+1
                for b in payload['destinations']] for a in payload['sources']]
        return {'durations':costs,'distances':costs}
    monkeypatch.setattr(routing,'_post_json',request)
    result=routing.road_matrix(points[0],points[1:])
    assert len(calls)==9
    assert result['durations'][54][1]==5402
    assert result['durations'][1][54]==155


def test_hourly_basis_can_cover_the_whole_workday(monkeypatch):
    monkeypatch.setattr(dispatch,'road_matrix',lambda h,s:matrix(2))
    payload={'title':'Hourly','contractor':'Example','home':{'lat':40,'lon':-80},
             'tasks':[{'external_task_id':'one','venue':'Example','address':'100 Example Road',
                       'lat':40.01,'lon':-80,'service_minutes':120}],
             'pricing':{'per_hour':'20','hourly_basis':'total_work'}}
    result=dispatch.plan(dispatch.PlanInput(**payload))
    assert result['total_hours']==3.5
    assert result['price']=='70.00'
