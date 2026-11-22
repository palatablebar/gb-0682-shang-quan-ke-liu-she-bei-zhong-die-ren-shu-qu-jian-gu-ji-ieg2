from copy import deepcopy
from app.config import REQUIRED,NUMERIC,DATES,TIMES,DEFAULT_MALL
from app.errors import DomainError
from app.modules.m2_mapping.coerce import number,day,instant,SENTINELS

def mall_of(row, parameters):
    # 商场归属：记录级 mall 列优先，缺省取运行参数 mall，再缺省为 default。
    # 含多场标记（如 M1|M2）的记录是跨场交集，整行隔离，不得进入正式区间。
    cell=str(row.get('mall','') or '').strip()
    if cell.upper() in SENTINELS:cell=str((parameters or {}).get('mall') or DEFAULT_MALL)
    parts=[p.strip() for p in cell.split('|') if p.strip()]
    if len(parts)!=1:raise DomainError('CROSS_MALL','跨场交集不得进入正式区间')
    return parts[0]

def normalize(state):
    state=deepcopy(state);good=[];groups={}
    for row in state['rows']:
        try:
            for field in REQUIRED:
                if str(row.get(field,'')).strip().upper() in SENTINELS:raise DomainError('MISSING','必填值缺失')
            item=dict(row);item['mall']=mall_of(row,state.get('parameters'))
            for field in NUMERIC:
                if field in item and item[field] != '':item[field]=number(item[field])
            for field in DATES:
                if field in item and item[field] != '':item[field]=day(item[field])
            for field in TIMES:
                if field in item and item[field] != '':item[field]=instant(item[field])
            key=(item['id'],str(item.get('version','1')))
            groups.setdefault(key,[]).append(item)
        except DomainError as error:state['quarantine'].append({'line':row['_line'],'id':row.get('id',''),'code':error.code,'raw':row['_raw']})
    for key,rows in groups.items():
        unique={str(sorted((k,str(v)) for k,v in r.items() if not k.startswith('_'))) for r in rows}
        if len(unique)>1:
            for r in rows:state['quarantine'].append({'line':r['_line'],'id':r['id'],'code':'CONFLICT','raw':r['_raw']})
        else:
            good.append(rows[0]);state['duplicate_count']+=len(rows)-1
    state['rows']=good
    return state
