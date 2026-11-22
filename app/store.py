import sqlite3,json,os,hashlib
from pathlib import Path
from datetime import datetime,timezone
from app.errors import DomainError
from app.config import RULE_VERSION,DEFAULT_MALL

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS formal (mall TEXT PRIMARY KEY, version INTEGER NOT NULL, run_id TEXT NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL, updated_at TEXT NOT NULL)')
    return db

def _promote(db, run_id, body):
    # 正式区间按商场独立晋升：只重算本批出现的商场；区间摘要未变则不新增版本。
    groups={}
    for calc in body.get('calculations',[]):groups.setdefault(calc.get('mall') or DEFAULT_MALL,[]).append(calc)
    for mall,calcs in sorted(groups.items()):
        calcs=sorted(calcs,key=lambda c:c['id'])
        digest=hashlib.sha256(json.dumps(calcs,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        row=db.execute('SELECT digest FROM formal WHERE mall=?',(mall,)).fetchone()
        if row and row['digest']==digest:continue
        payload={'mall':mall,'calculations':calcs,'business':(body.get('business',{}).get('by_mall') or {}).get(mall,{})}
        now=datetime.now(timezone.utc).isoformat()
        if row:db.execute('UPDATE formal SET version=version+1,run_id=?,digest=?,body=?,updated_at=? WHERE mall=?',(run_id,digest,json.dumps(payload,ensure_ascii=False),now,mall))
        else:db.execute('INSERT INTO formal(mall,version,run_id,digest,body,updated_at) VALUES (?,?,?,?,?,?)',(mall,1,run_id,digest,json.dumps(payload,ensure_ascii=False),now))

def save(run_id, body):
    with connection() as db:
        # 原始观测只插不改；正式区间切换与入库同事务，任一步失败整体回滚。
        db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',(run_id,json.dumps(body,ensure_ascii=False)))
        _promote(db,run_id,body)
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    return get(run_id)

def get(run_id):
    with connection() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','批次不存在',404)
    body=json.loads(r['body']);body.update(run_id=r['id'],review_version=r['version'],decision=r['decision']);return body

def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def formal():
    with connection() as db:rows=db.execute('SELECT mall,version,run_id,digest,updated_at FROM formal ORDER BY mall').fetchall()
    return [dict(r) for r in rows]

def formal_state():
    # 估界页面与封存导出的同一数据源：当前各商场正式区间的并集。
    with connection() as db:rows=db.execute('SELECT * FROM formal ORDER BY mall').fetchall()
    if not rows:return None
    runs={}
    for r in rows:
        if r['run_id'] not in runs:
            try:runs[r['run_id']]=get(r['run_id'])
            except DomainError:runs[r['run_id']]=None
    runs={k:v for k,v in runs.items() if v}
    calculations=[];by_mall={}
    for r in rows:
        body=json.loads(r['body']);by_mall[r['mall']]=body.get('business',{})
        for calc in body.get('calculations',[]):calculations.append(dict(calc,mall=r['mall'],formal_version=r['version']))
    calculations.sort(key=lambda c:(c['mall'],c['id']))
    quarantine=[];seen=set();raw_rows=[];input_count=0;balanced=True
    for rid,run in runs.items():
        input_count+=run.get('input_count',0);raw_rows.extend(run.get('rows',[]))
        balanced=balanced and run.get('reconcile',{}).get('balanced',False)
        for q in run.get('quarantine',[]):
            key=(rid,q.get('line'),q.get('code'))
            if key not in seen:seen.add(key);quarantine.append(q)
    business={'total':sum(c['lower'] for c in calculations),'upper_total':sum(c['upper'] for c in calculations),'buckets':len(calculations),'unit':'客次','by_mall':by_mall}
    malls={r['mall']:{'version':r['version'],'run_id':r['run_id']} for r in rows}
    state={'run_id':'formal','rows':raw_rows,'quarantine':quarantine,'input_count':input_count,
        'blank_count':0,'header_count':0,'duplicate_count':0,'calculations':calculations,'business':business,
        'manifest':{'formal':True,'rule_version':RULE_VERSION,'timezone':'Asia/Shanghai','parameters':{},
            'generated_at':datetime.now(timezone.utc).isoformat(),'malls':malls},
        'reconcile':{'formal':True,'balanced':balanced,'business':business,'malls':malls,'sources':sorted(runs)}}
    from app.modules.m7_archive.export_out import checksum
    state['archive']={'sha256':checksum(state),'evidence_count':len(calculations)+len(quarantine),'ready':balanced}
    return state

def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:
            if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():raise DomainError('NOT_FOUND','批次不存在',404)
            raise DomainError('STALE','复核版本已更新，请刷新',409)
    return get(run_id)
