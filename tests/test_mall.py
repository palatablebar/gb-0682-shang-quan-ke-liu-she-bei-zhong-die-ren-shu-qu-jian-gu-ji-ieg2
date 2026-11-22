import csv,io
import pytest
from app import store
from app.workflow import load,run
from app.errors import DomainError

@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'mall.db'))

def malls():return run(load('malls.csv'),{})
def patch():return run(load('malls-m2.csv'),{})

def test_formal_intervals_keep_mall_attribution():
    # 同标签A/B/C不跨场合并：每场各自成区间
    s=malls();by={(c['mall'],c['id']) for c in s['calculations']}
    assert len(s['calculations'])==6 and len(by)==6
    m1=sorted((c for c in s['calculations'] if c['mall']=='M1'),key=lambda c:c['id'])
    m2=sorted((c for c in s['calculations'] if c['mall']=='M2'),key=lambda c:c['id'])
    assert (m1[0]['lower'],m1[0]['upper'])==(180,190)
    assert (m2[0]['lower'],m2[0]['upper'])==(340,370)
    assert s['business']['by_mall']['M1']['buckets']==3 and s['business']['by_mall']['M2']['buckets']==3

def test_same_tag_different_mall_not_merged():
    s=run(load('malls.csv'),{},3)
    obs=[o for o in s['observations'] if o['kind']=='zone' and o['left']=='A' and o['bucket']=='2026-11-16T01:00:00+00:00']
    assert sorted(o['count'] for o in obs)==[100,200]

def test_mall_resolution_default_and_parameter():
    assert {r['mall'] for r in run(load('clean.csv'),{})['rows']}=={'default'}
    assert {r['mall'] for r in run(load('clean.csv'),{'mall':'M9'})['rows']}=={'M9'}

def test_cross_mall_pair_never_enters_formal(client):
    s=malls()
    assert sum(q['code']=='CROSS_MALL' for q in s['quarantine'])==1
    assert all('|' not in c['mall'] for c in s['calculations'])
    r=client.post('/api/runs',json={'sample':'malls.csv'});assert r.status_code==200
    formal=store.formal_state()
    assert formal and all('|' not in c['mall'] for c in formal['calculations'])
    text=client.get('/api/archive/'+r.get_json()['run_id']+'/export.csv').data.decode('utf-8-sig')
    assert 'CROSS_MALL' in text
    calc_rows=[row for row in csv.reader(io.StringIO(text)) if row[0]=='计算']
    assert calc_rows and all('|' not in row[2] for row in calc_rows)

def test_page_and_export_share_grouping(client):
    client.post('/api/runs',json={'sample':'malls.csv'})
    formal=store.formal_state()
    page=client.get('/m5_calculate').data.decode()
    malls_order=list(dict.fromkeys(c['mall'] for c in formal['calculations']))
    positions=[page.index('商场 '+m) for m in malls_order]
    assert positions==sorted(positions)
    latest=client.get('/api/runs').get_json()[0]['id']
    text=client.get('/api/archive/'+latest+'/export.csv').data.decode('utf-8-sig')
    calc=[(row[2],row[4]) for row in csv.reader(io.StringIO(text)) if row[0]=='计算']
    assert calc==[(c['mall'],c['id']) for c in formal['calculations']]

def test_patch_recomputes_only_affected_mall(db):
    s1=malls();store.save(s1['run_id'],s1)
    before={f['mall']:f for f in store.formal()}
    assert before['M1']['version']==1 and before['M2']['version']==1
    s2=patch();store.save(s2['run_id'],s2)
    after={f['mall']:f for f in store.formal()}
    assert after['M1']['version']==1 and after['M1']['digest']==before['M1']['digest']
    assert after['M2']['version']==2 and after['M2']['digest']!=before['M2']['digest']
    formal=store.formal_state()
    m1=sorted((c for c in formal['calculations'] if c['mall']=='M1'),key=lambda c:c['id'])
    m2=sorted((c for c in formal['calculations'] if c['mall']=='M2'),key=lambda c:c['id'])
    assert m1[0]['lower']==180 and m1[0]['formal_version']==1
    assert m2[0]['lower']==350 and m2[0]['formal_version']==2

def test_same_batch_rerun_adds_no_version(db):
    s=malls();store.save(s['run_id'],s)
    versions={f['mall']:f['version'] for f in store.formal()}
    store.save(s['run_id'],s)
    assert {f['mall']:f['version'] for f in store.formal()}==versions
    assert len(store.history())==1 and store.get(s['run_id'])['review_version']==1

def test_failed_switch_keeps_old_version(db,monkeypatch):
    s1=malls();store.save(s1['run_id'],s1)
    before=store.formal()
    real=store._promote
    def failing(db,run_id,body):real(db,run_id,body);raise DomainError('SWITCH','模拟切换失败')
    monkeypatch.setattr(store,'_promote',failing)
    s2=patch()
    with pytest.raises(DomainError):store.save(s2['run_id'],s2)
    assert store.formal()==before and len(store.history())==1

def test_page_uses_old_version_after_failed_switch(client,monkeypatch):
    client.post('/api/runs',json={'sample':'malls.csv'})
    old=client.get('/m5_calculate').data.decode()
    assert '340' in old and '350' not in old
    real=store._promote
    def failing(db,run_id,body):real(db,run_id,body);raise DomainError('SWITCH','模拟切换失败')
    monkeypatch.setattr(store,'_promote',failing)
    assert client.post('/api/runs',json={'sample':'malls-m2.csv'}).status_code==422
    page=client.get('/m5_calculate').data.decode()
    assert '340' in page and '350' not in page

def test_raw_observations_never_overwritten(db):
    s=malls();store.save(s['run_id'],s)
    original=store.get(s['run_id'])
    tampered=dict(s,rows=[],quarantine=[])
    store.save(s['run_id'],tampered)
    assert store.get(s['run_id'])['rows']==original['rows'] and len(original['rows'])==36
    s2=patch();store.save(s2['run_id'],s2)
    assert store.get(s['run_id'])['rows']==original['rows']
    assert store.get(s['run_id'])['calculations']==original['calculations']
