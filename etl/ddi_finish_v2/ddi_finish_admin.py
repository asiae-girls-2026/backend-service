#!/usr/bin/env python3
"""Explicit scoped review CLI. Uses existing DATABASE_URL; no automatic approval."""
import argparse
import json
import os
from pathlib import Path
import sys

from ddi_review_runtime import load_source, product_info, scope_key, source_hash, rows, reasons, approved_for_pair

DDL='''
CREATE TABLE IF NOT EXISTS public.ddi_scoped_approval (
 approval_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 target_type text NOT NULL CHECK(target_type IN ('PRODUCT_RULE','PRODUCT_D','D_PAIR')),
 target_key text NOT NULL,
 scope_pair text NOT NULL,
 lifecycle text NOT NULL CHECK(lifecycle IN ('APPROVED','ACTIVE','WITHDRAWN')),
 source_hash text NOT NULL,
 source_snapshot jsonb NOT NULL,
 product_snapshot jsonb NOT NULL,
 reviewer text NOT NULL,
 reason text NOT NULL,
 evidence_ref text NOT NULL,
 rule_release_id bigint REFERENCES public.rule_release(rule_release_id),
 reviewed_at timestamptz NOT NULL DEFAULT now(),
 activated_at timestamptz,
 withdrawn_at timestamptz,
 UNIQUE(target_type,target_key,scope_pair),
 CHECK(lifecycle <> 'ACTIVE' OR rule_release_id IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS public.ddi_scoped_approval_audit (
 audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 approval_id bigint NOT NULL REFERENCES public.ddi_scoped_approval(approval_id),
 action text NOT NULL,
 actor text NOT NULL,
 reason text NOT NULL,
 before_data jsonb NOT NULL,
 after_data jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ddi_scoped_active_idx
 ON public.ddi_scoped_approval(scope_pair,lifecycle);
'''


def require(condition,message):
    if not condition: raise ValueError(message)


def jsonb(value):
    from psycopg.types.json import Jsonb
    return Jsonb(json.loads(json.dumps(value,default=str)))


def target_key(kind,key):
    if kind=='PRODUCT_RULE': return str(int(key))
    a,b=key.split(':')
    if kind=='PRODUCT_D': return f'{int(a)}:{b.strip()}'
    return ':'.join(sorted([a.strip(),b.strip()]))


def approval(conn,kind,key,scope):
    r=rows(conn,'''SELECT to_jsonb(a) AS data FROM public.ddi_scoped_approval a
        WHERE target_type=%s AND target_key=%s AND scope_pair=%s FOR UPDATE''',(kind,key,scope))
    return r[0] if r else None


def validate_target(kind,source,pair):
    if kind=='PRODUCT_RULE':
        require(source.get('interaction_type')=='DDI' and source.get('subject_type')=='PRODUCT' and source.get('object_type')=='PRODUCT','직접 제품 DDI 규칙만 지원합니다.')
        require(sorted([source['subject_id'],source['object_id']])==pair,'제품 규칙의 대상과 --pair가 다릅니다.')
        require(bool(reasons(source)),'규칙의 원천 병용금기 주의내용이 없습니다.')
    elif kind=='PRODUCT_D':
        require(source['med_product_id'] in pair,'매핑 제품이 --pair에 없습니다.')
    else:
        require(source.get('rule_type')=='DDI','DDI 성분쌍이 아닙니다.')
        require(bool(reasons(source)),'성분쌍의 원천 병용금기 주의내용을 읽을 수 없습니다.')


def audit(conn,before,after,action,args):
    conn.execute('''INSERT INTO public.ddi_scoped_approval_audit
      (approval_id,action,actor,reason,before_data,after_data)
      VALUES(%s,%s,%s,%s,%s,%s)''',
      (after['approval_id'],action,args.actor,args.reason,jsonb(before or {}),jsonb(after)))


def mirror(conn,kind,key):
    # Aggregate shared source-row flags across scoped approvals. Never withdraw
    # another scope merely because one scope was withdrawn.
    approved=rows(conn,'''SELECT to_jsonb(a) AS data FROM public.ddi_scoped_approval a
      WHERE target_type=%s AND target_key=%s AND lifecycle IN ('APPROVED','ACTIVE')''',(kind,key))
    active=[a for a in approved if a['lifecycle']=='ACTIVE']
    review='APPROVED' if approved else 'PENDING'
    if kind=='PRODUCT_RULE':
        conn.execute('''UPDATE public.interaction_rule SET review_status=%s,active=%s,
          rule_release_id=%s WHERE interaction_rule_id=%s''',
          (review,bool(active),active[0]['rule_release_id'] if active else None,int(key)))
    elif kind=='PRODUCT_D':
        product,code=key.split(':')
        conn.execute('''UPDATE public.med_dur_product_mapping SET review_status=%s
          WHERE med_product_id=%s AND dur_code=%s''',(review,int(product),code))
    else:
        a,b=key.split(':')
        conn.execute('''UPDATE public.med_dur_ingredient_rule_stage SET review_status=%s,active=%s
          WHERE rule_type='DDI' AND ((dur_code_a=%s AND dur_code_b=%s)
          OR (dur_code_a=%s AND dur_code_b=%s))''',(review,bool(active),a,b,b,a))


def get_release(conn,code,args):
    require(bool(code.strip()) and len(code)<=80,'릴리스 코드는 1~80자여야 합니다.')
    conn.execute('LOCK TABLE public.rule_release IN SHARE ROW EXCLUSIVE MODE')
    r=rows(conn,'SELECT to_jsonb(r) AS data FROM public.rule_release r WHERE release_code=%s FOR UPDATE',(code,))
    if not r:
        seq=conn.execute("SELECT pg_get_serial_sequence('public.rule_release','rule_release_id')").fetchone()[0]
        if seq:
            rid=conn.execute("INSERT INTO public.rule_release(release_code,status,notes) VALUES(%s,'ACTIVE',%s) RETURNING rule_release_id",(code,'Scoped DDI release; actor='+args.actor)).fetchone()[0]
        else:
            rid=conn.execute('SELECT COALESCE(MAX(rule_release_id),0)+1 FROM public.rule_release').fetchone()[0]
            conn.execute("INSERT INTO public.rule_release(rule_release_id,release_code,status,notes) VALUES(%s,%s,'ACTIVE',%s)",(rid,code,'Scoped DDI release; actor='+args.actor))
        conn.execute('UPDATE public.rule_release SET released_at=now() WHERE rule_release_id=%s',(rid,))
        return rid
    require(len(r)==1 and r[0]['status']=='ACTIVE','기존 ACTIVE 릴리스 또는 새로운 코드만 사용하세요. DRAFT/RETIRED는 일괄 활성화하지 않습니다.')
    return r[0]['rule_release_id']


def mutate(conn,args):
    pair=sorted(args.pair); scope=scope_key(pair); key=target_key(args.kind,args.key)
    for name in ('actor','reason'):
        require(bool(getattr(args,name).strip()),f'{name}이 비었습니다.')
    conn.execute('SELECT pg_advisory_xact_lock(%s)',(2026102702,))
    source=load_source(conn,args.kind,key,lock=True)
    before=approval(conn,args.kind,key,scope)
    if args.command=='approve':
        require(bool(args.evidence_ref.strip()),'근거 참조가 필요합니다.')
        require(not before or before['lifecycle']!='ACTIVE','먼저 해당 범위를 withdraw한 뒤 재승인하세요.')
        validate_target(args.kind,source,pair)
        identities=product_info(conn,pair)
        # A manually enabled legacy product rule must be explicitly withdrawn
        # before taking it over; do not silently deactivate existing service data.
        if args.kind=='PRODUCT_RULE' and source.get('active') and not before:
            raise ValueError('관리 도구 외부에서 이미 활성화된 제품 규칙입니다. 기존 승인 기록과 먼저 조정해야 합니다.')
        conn.execute('''INSERT INTO public.ddi_scoped_approval
          (target_type,target_key,scope_pair,lifecycle,source_hash,source_snapshot,
           product_snapshot,reviewer,reason,evidence_ref)
          VALUES(%s,%s,%s,'APPROVED',%s,%s,%s,%s,%s,%s)
          ON CONFLICT(target_type,target_key,scope_pair) DO UPDATE SET
           lifecycle='APPROVED',source_hash=EXCLUDED.source_hash,
           source_snapshot=EXCLUDED.source_snapshot,product_snapshot=EXCLUDED.product_snapshot,
           reviewer=EXCLUDED.reviewer,reason=EXCLUDED.reason,evidence_ref=EXCLUDED.evidence_ref,
           rule_release_id=NULL,reviewed_at=now(),activated_at=NULL,withdrawn_at=NULL''',
          (args.kind,key,scope,source_hash(source),jsonb(source),jsonb(identities),args.actor,args.reason,args.evidence_ref))
    elif args.command=='activate':
        require(before is not None and before['lifecycle']=='APPROVED','먼저 해당 대상·제품쌍을 approve하세요. 철회 후에도 재승인이 필요합니다.')
        require(before['source_hash']==source_hash(source),'승인 이후 원천이 변경됐습니다. 재승인이 필요합니다.')
        require(before['product_snapshot']==product_info(conn,pair),'승인 이후 제품 정보가 변경됐습니다. 재승인이 필요합니다.')
        validate_target(args.kind,source,pair)
        rid=get_release(conn,args.release_code,args)
        conn.execute("UPDATE public.ddi_scoped_approval SET lifecycle='ACTIVE',rule_release_id=%s,activated_at=now() WHERE approval_id=%s",(rid,before['approval_id']))
    else:
        require(before is not None,'해당 대상·제품쌍의 승인 기록이 없습니다.')
        conn.execute("UPDATE public.ddi_scoped_approval SET lifecycle='WITHDRAWN',withdrawn_at=now() WHERE approval_id=%s",(before['approval_id'],))
    after=approval(conn,args.kind,key,scope)
    mirror(conn,args.kind,key)
    audit(conn,before,after,args.command.upper(),args)
    return {'action':args.command,'target_type':args.kind,'target_key':key,'scope_pair':scope,
            'lifecycle':after['lifecycle'],'rule_release_id':after['rule_release_id']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    for command in ('approve','activate','withdraw','inspect','history'):
        s=sub.add_parser(command)
        s.add_argument('--kind',choices=('PRODUCT_RULE','PRODUCT_D','D_PAIR'),required=True)
        s.add_argument('--key',required=True)
        s.add_argument('--pair',type=int,nargs=2,required=True)
        if command in ('approve','activate','withdraw'):
            s.add_argument('--actor',required=True);s.add_argument('--reason',required=True)
        if command=='approve':s.add_argument('--evidence-ref',required=True)
        if command=='activate':s.add_argument('--release-code',required=True)
    s=sub.add_parser('check');s.add_argument('--pair',type=int,nargs=2,required=True)
    args=p.parse_args()
    try:
        import psycopg
        require(bool(os.environ.get('DATABASE_URL')),'기존 API 컨테이너 안에서 실행하세요.')
        with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=10) as conn:
            if args.command in ('inspect','history','check'):conn.execute('SET TRANSACTION READ ONLY')
            conn.execute("SET LOCAL lock_timeout='5s'");conn.execute("SET LOCAL statement_timeout='30s'")
            if args.command=='init':
                conn.execute(DDL);result={'action':'INIT','rules_changed':False}
            elif args.command=='check':
                result={'confirmed_rules':approved_for_pair(conn,*args.pair),'coverage':'INCOMPLETE'}
            elif args.command in ('inspect','history'):
                key=target_key(args.kind,args.key);scope=scope_key(args.pair)
                if args.command=='inspect':
                    source=load_source(conn,args.kind,key)
                    result={'source':source,'source_hash':source_hash(source),'official_reasons':reasons(source),
                       'approvals':rows(conn,'''SELECT to_jsonb(a) AS data FROM public.ddi_scoped_approval a
                         WHERE target_type=%s AND target_key=%s AND scope_pair=%s''',(args.kind,key,scope))}
                else:
                    result=rows(conn,'''SELECT to_jsonb(h) AS data FROM public.ddi_scoped_approval_audit h
                      JOIN public.ddi_scoped_approval a ON a.approval_id=h.approval_id
                      WHERE a.target_type=%s AND a.target_key=%s AND a.scope_pair=%s ORDER BY h.audit_id''',(args.kind,key,scope))
            else:result=mutate(conn,args)
        print(json.dumps(result,ensure_ascii=False,indent=2,default=str));return 0
    except Exception as exc:
        if isinstance(exc,(ValueError,ImportError)):
            print(f'작업 중단: {exc}',file=sys.stderr)
        else:print(f'작업 실패·롤백: {type(exc).__name__}, SQLSTATE={getattr(exc,"sqlstate",None)}',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
