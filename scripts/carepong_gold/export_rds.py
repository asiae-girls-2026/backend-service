"""Run inside infra-api-1. Read-only RDS -> S3; no credentials in source.
Hourly recent-eight-day snapshots, plus late inserts since previous checkpoint.
Existing row updates without received_at changes require --date YYYY-MM-DD.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
import uuid
from datetime import date,datetime,timedelta,timezone

def main():
    import boto3
    import psycopg
    from psycopg.rows import dict_row
    from botocore.config import Config
    from botocore.exceptions import ClientError
    ap=argparse.ArgumentParser();ap.add_argument('--date');opt=ap.parse_args()
    kst=timezone(timedelta(hours=9));today=datetime.now(kst).date()
    bucket='carepong-raw-2026';state_key='raw/rds-export/_state/export.json'
    s3=boto3.client('s3',region_name='ap-northeast-2',config=Config(
       connect_timeout=10,read_timeout=60,retries={'mode':'standard','max_attempts':3}))
    url=os.environ.get('DATABASE_URL')
    if not url:
        from app.services import db
        url=db.DATABASE_URL
    if not url:
        raise SystemExit('DATABASE_URL missing')
    def get(key):
        try:
            with s3.get_object(Bucket=bucket,Key=key)['Body'] as body:
                return json.loads(body.read())
        except ClientError as e:
            if e.response['Error']['Code'] in ('NoSuchKey','404'):
                return None
            raise
    def put(key,obj):
        s3.put_object(Bucket=bucket,Key=key,Body=json.dumps(obj,default=str).encode(),ContentType='application/json')
    def serialize(v):
        if isinstance(v,datetime):return v.isoformat()
        if isinstance(v,uuid.UUID):return str(v)
        raise TypeError(type(v).__name__)
    days={opt.date} if opt.date else {(today-timedelta(days=n)).isoformat() for n in range(1,9)
           if today-timedelta(days=n)>=date(2026,10,28)}
    state=get(state_key) if not opt.date else None
    with psycopg.connect(url,connect_timeout=10,options='-c default_transaction_read_only=on -c statement_timeout=60000') as c:
        with c.cursor() as cur:
            cur.execute('SELECT CURRENT_TIMESTAMP');checkpoint=cur.fetchone()[0]
            if state and not opt.date:
                since=datetime.fromisoformat(state['received_through'])-timedelta(hours=1)
                cur.execute("""SELECT DISTINCT export_day FROM (
                  SELECT (window_start AT TIME ZONE 'Asia/Seoul')::date AS export_day FROM windows
                  WHERE received_at >= %s AND received_at <= %s
                  UNION
                  SELECT (coalesce(w.window_start,r.answered_at) AT TIME ZONE 'Asia/Seoul')::date
                  FROM self_reports r LEFT JOIN windows w
                  ON w.window_id=r.window_id AND w.user_id=r.user_id
                  WHERE r.received_at >= %s AND r.received_at <= %s
                ) d""",(since,checkpoint,since,checkpoint))
                days|={r[0].isoformat() for r in cur.fetchall() if date(2026,10,28)<=r[0]<today}
    run=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
    sqls={
      'windows':"""SELECT u.cp_number person_id,w.user_id,w.session_id,w.window_id,
       w.window_start,w.window_sec,w.features,w.activity_zero,w.dnd,w.model_version,
       w.client_event_id,w.received_at FROM windows w JOIN users u ON u.user_id=w.user_id
       WHERE w.window_start >= %s AND w.window_start < %s ORDER BY w.window_start,w.window_id""",
      'self_reports':"""SELECT r.report_id,u.cp_number person_id,r.user_id,r.window_id,
       w.session_id,w.window_start,r.trigger,r.yesno,r.level_1to5,r.status,
       r.prompt_time,r.clicked_at,r.answered_at,r.client_event_id,r.received_at
       FROM self_reports r JOIN users u ON u.user_id=r.user_id
       LEFT JOIN windows w ON w.window_id=r.window_id AND w.user_id=r.user_id
       WHERE coalesce(w.window_start,r.answered_at) >= %s
       AND coalesce(w.window_start,r.answered_at) < %s ORDER BY r.answered_at,r.report_id"""}
    for day in sorted(days):
        start=datetime.combine(date.fromisoformat(day),datetime.min.time(),tzinfo=kst)
        prefix=f'raw/rds-export/dt={day}/run={run}/';files=[]
        with psycopg.connect(url,connect_timeout=10,options='-c default_transaction_read_only=on -c statement_timeout=60000') as c:
            c.isolation_level=psycopg.IsolationLevel.REPEATABLE_READ
            with c.cursor() as cur:
                cur.execute('SELECT CURRENT_TIMESTAMP');snap=cur.fetchone()[0].isoformat()
            for name,sql in sqls.items():
                buffer=io.BytesIO();count=0
                with gzip.GzipFile(fileobj=buffer,mode='wb',mtime=0) as gz:
                    with c.cursor(name='export_'+name,row_factory=dict_row) as cur:
                        cur.execute(sql,(start,start+timedelta(days=1)))
                        while True:
                            rows=cur.fetchmany(1000)
                            if not rows:break
                            for row in rows:
                                gz.write((json.dumps(row,ensure_ascii=False,default=serialize,allow_nan=False)+'\n').encode())
                                count+=1
                data=buffer.getvalue();key=prefix+name+'.ndjson.gz'
                s3.put_object(Bucket=bucket,Key=key,Body=data,ContentType='application/x-ndjson',ContentEncoding='gzip')
                files.append({'key':key,'rows':count,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        manifest_key=prefix+'_SUCCESS.json'
        put(manifest_key,{'export_schema':'rds-export-v1','dt':day,'timezone':'Asia/Seoul',
            'run_id':run,'snapshot_at':snap,'completed_at':datetime.now(timezone.utc).isoformat(),'files':files})
        put(f'raw/rds-export/_latest/dt={day}.json',{'manifest_key':manifest_key,'run_id':run})
        print('EXPORT_SUCCESS',day,','.join(f"{f['rows']} rows" for f in files),flush=True)
    if not opt.date:
        put(state_key,{'received_through':checkpoint.isoformat(),'run_id':run})
    print('EXPORT_COMPLETE',flush=True)

if __name__=='__main__':
    main()
