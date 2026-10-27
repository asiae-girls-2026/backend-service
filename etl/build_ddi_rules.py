#!/usr/bin/env python3
"""CarePong DUR -> existing interaction_rule, safe MVP.

Default is read-only dry-run. --apply only publishes direct official product pairs;
ingredient pairs require both existing D-M mappings APPROVED and no unknown
application conditions. All provenance is preserved via source_record_id and
JSON target_text. No raw re-collection, no bulk modification of existing rows.
"""
import argparse
import csv
import gzip
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path

import psycopg

BASE = Path('/home/ubuntu/backend-service/etl')
DATA = BASE / 'data' / 'dur'
SOURCE = 'MFDS_DUR_MVP'
# Field names verified from the previous handoff. Unknown supplemental fields
# will be preserved but cause conservative REVIEW status.
ALLOWED = {'DUR_SEQ','TYPE_CODE','INGR_CODE','MIXTURE_INGR_CODE',
           'ITEM_SEQ','MIXTURE_ITEM_SEQ','INGR_KOR_NAME','INGR_ENG_NAME',
           'MIXTURE_INGR_KOR_NAME','MIXTURE_INGR_ENG_NAME',
           'MAIN_INGR','MIXTURE_MAIN_INGR'}
CONDITION_TERMS = ('ROUTE','DOSAGE','FORM','AGE','PREG','SEX','DOSE','CONDITION',
                   'PERIOD','REMARK','NOTICE','ETC','NOTE','CONT','REASON','WARN')


def trim(x):
    return str(x).strip() if x is not None else ''


def objects(path):
    opener = gzip.open if path.name.endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8-sig') as f:
        if path.suffix.lower() == '.csv':
            yield from csv.DictReader(f)
        else:
            # Supports raw JSON pages and NDJSON (including .json.gz/.jsonl.gz)
            content = f.read().strip()
            if not content:
                return
            try:
                root = json.loads(content)
                yield from descend(root)
            except json.JSONDecodeError:
                for line in content.splitlines():
                    if line.strip():
                        yield from descend(json.loads(line))


def descend(node):
    if isinstance(node, list):
        for v in node:
            yield from descend(v)
    elif isinstance(node, dict):
        up = {str(k).upper(): v for k, v in node.items()}
        if 'INGR_CODE' in up and 'MIXTURE_INGR_CODE' in up:
            yield up
        else:
            for val in node.values():
                if isinstance(val, (dict, list)):
                    yield from descend(val)


def discover(path):
    if path.is_file():
        return [path]
    if not path.exists():
        raise SystemExit(f'MISSING_DUR_LOCAL: {path}\nRestore the existing S3 raw files; do NOT call the public API again.')
    files = [p for p in path.rglob('*') if p.is_file() and
             (p.name.lower().endswith(('.json','.jsonl','.ndjson','.csv','.json.gz','.jsonl.gz','.ndjson.gz')))]
    if not files:
        raise SystemExit(f'NO_SUPPORTED_DUR_FILES: {path}')
    return sorted(files)


def candidate(row, item_to_id, approved):
    a = trim(row.get('ITEM_SEQ'))
    b = trim(row.get('MIXTURE_ITEM_SEQ'))
    da = trim(row.get('INGR_CODE'))
    db = trim(row.get('MIXTURE_INGR_CODE'))
    # Product pair: identical official item IDs. This cannot be generalized.
    if a in item_to_id and b in item_to_id and a != b:
        x, y = sorted((item_to_id[a], item_to_id[b]))
        return 'PRODUCT', x, 'PRODUCT', y, 'DIRECT_PRODUCT', True
    # D-M mappings currently all pending. Never turn a candidate into a fact.
    ma, mb = approved.get(da, set()), approved.get(db, set())
    if len(ma) == 1 and len(mb) == 1 and da != db:
        # M source codes are textual, and this MVP avoids creating unknown
        # canonical_ingredient IDs. Review code pairs remain staged only.
        return None, None, None, None, 'APPROVED_M_CODES_REQUIRE_CANONICAL_MAPPING', False
    return None, None, None, None, 'NOT_DIRECT_OR_APPROVED', False


def normalized_key(row):
    # Symmetric by product pair, but preserve condition and source identity.
    return json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dur-dir', type=Path, default=DATA)
    ap.add_argument('--apply', action='store_true', help='Persist verified product pairs only')
    ap.add_argument('--limit-files', type=int, default=0)
    args = ap.parse_args()
    files = discover(args.dur_dir)
    if args.limit_files:
        if args.apply:
            raise SystemExit('--limit-files cannot be used with --apply')
        files = files[:args.limit_files]
    url = os.getenv('DATABASE_URL')
    if not url:
        raise SystemExit('DATABASE_URL is required. Run inside existing infra-api-1 container.')
    with psycopg.connect(url, connect_timeout=10) as conn:
        if not args.apply:
            conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SELECT set_config('statement_timeout', %s, true)", ('120s',))
        item_to_id = dict(conn.execute('SELECT item_seq, med_product_id FROM public.med_product'))
        mappings = conn.execute("""SELECT dur_code,mtral_code,review_status
                                  FROM public.med_dur_code_mapping""").fetchall()
        approved = {}
        for d, m, state in mappings:
            if state == 'APPROVED':
                approved.setdefault(d, set()).add(m)
        counts = Counter()
        selected = {}
        for fp in files:
            counts['files'] += 1
            for row in objects(fp):
                counts['rows'] += 1
                if trim(row.get('TYPE_CODE')).upper() != 'A':
                    counts['non_A'] += 1
                    continue
                result = candidate(row, item_to_id, approved)
                if not result[-1]:
                    counts[result[-2]] += 1
                    continue
                s_type, sid, o_type, oid, method, _ = result
                # Conservative release policy: source conditions not yet modeled.
                supplementary = {k: v for k, v in row.items()
                                 if k not in ALLOWED and trim(v)}
                condition_keys = {k: v for k, v in supplementary.items()
                                  if any(term in k for term in CONDITION_TERMS)}
                if condition_keys:
                    # Preserve all supplemental fields in target_text.
                    # Stage the rule for review; never activate automatically.
                    counts['staged_with_supplementary_fields'] += 1
                seq = trim(row.get('DUR_SEQ'))
                if not seq:
                    counts['missing_dur_seq'] += 1
                    continue
                evidence = (trim(row.get('INGR_KOR_NAME')) + ' / ' +
                            trim(row.get('MIXTURE_INGR_KOR_NAME'))).strip(' /')
                if not evidence:
                    counts['no_evidence'] += 1
                    continue
                detail = {'dur_seq': seq, 'item_seq': trim(row.get('ITEM_SEQ')),
                          'mixture_item_seq': trim(row.get('MIXTURE_ITEM_SEQ')),
                          'ingr_code': trim(row.get('INGR_CODE')),
                          'mixture_ingr_code': trim(row.get('MIXTURE_INGR_CODE')),
                          'file': fp.name, 'additional_fields': supplementary,
                          'scope': method, 'caveat': 'Check official DUR conditions'}
                evidence_key = hashlib.sha256(normalized_key(row).encode()).hexdigest()[:24]
                # Keep distinct evidence/conditions distinct; never dedup by DUR_SEQ alone.
                source_id = f'{seq}:{evidence_key}'
                selected[(s_type, sid, o_type, oid, source_id)] = (evidence, detail)
        print('COUNTS', json.dumps(counts, ensure_ascii=False, sort_keys=True))
        print('DIRECT_PRODUCT_RULES', len(selected))
        print('APPROVED_D_M_CODES', len(approved))
        if not args.apply:
            print('DRY_RUN_ONLY. RDS was not modified.')
            return
        if not selected:
            raise SystemExit('No validated rule; nothing written.')
        # Idempotent insert. Serialize writer; do not touch pre-existing rules.
        conn.execute("SELECT set_config('lock_timeout', %s, true)", ('3s',))
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (20261023,))
        max_id = conn.execute('SELECT COALESCE(MAX(interaction_rule_id),0) FROM public.interaction_rule').fetchone()[0]
        inserted = 0
        for (stype, sid, otype, oid, source_id), (evidence, detail) in sorted(selected.items()):
            exists = conn.execute('''SELECT 1 FROM public.interaction_rule
              WHERE source_dataset=%s AND source_record_id=%s
                AND subject_type=%s AND subject_id=%s
                AND object_type=%s AND object_id=%s LIMIT 1''',
                (SOURCE, source_id, stype, sid, otype, oid)).fetchone()
            if exists:
                continue
            max_id += 1
            conn.execute('''INSERT INTO public.interaction_rule
              (interaction_rule_id,subject_type,subject_id,object_type,object_id,
               interaction_type,severity,action_type,evidence_text,
               source_dataset,source_record_id,review_status,active,target_text)
              VALUES (%s,%s,%s,%s,%s,'DDI','CONTRAINDICATED','AVOID_COMBINATION',
                      %s,%s,%s,'PENDING',false,%s)''',
              (max_id, stype, sid, otype, oid,evidence,SOURCE,source_id,
               json.dumps(detail,ensure_ascii=False)))
            inserted += 1
        conn.commit()
        print('INSERTED_REVIEW_INACTIVE', inserted)
        print('No automatic active rule releases performed.')

if __name__ == '__main__':
    main()
