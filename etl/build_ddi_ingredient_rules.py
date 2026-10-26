#!/usr/bin/env python3
"""Stage ingredient-level DUR rules from explicitly approved mappings.

No automatic approval or activation.
Default: dry-run. --apply: PENDING inactive INSERT only.
"""
import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg
from build_ddi_rules import discover, objects, trim

SOURCE = "MFDS_DUR_INGREDIENT_MVP"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dur-dir", type=Path, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    with psycopg.connect(
        os.environ["DATABASE_URL"],
        connect_timeout=10
    ) as conn:
        if not args.apply:
            conn.execute("SET TRANSACTION READ ONLY")

        conn.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            ("120s",)
        )

        # D -> M: explicitly approved relations only
        approved = defaultdict(set)
        for d, m in conn.execute("""
            SELECT dur_code, mtral_code
            FROM public.med_dur_code_mapping
            WHERE review_status = 'APPROVED'
        """):
            approved[d].add(m)

        # M -> canonical ingredient: approved product mappings only
        m_to_i = defaultdict(set)
        for m, iid in conn.execute("""
            SELECT DISTINCT source_code, ingredient_id
            FROM public.med_product_ingredient
            WHERE source_code IS NOT NULL
              AND ingredient_id IS NOT NULL
              AND mapping_status = 'APPROVED'
        """):
            m_to_i[m].add(int(iid))

        # Resolve only unambiguous D -> M -> ingredient chains
        resolved = {}
        for d, ms in approved.items():
            if len(ms) != 1:
                continue
            m = next(iter(ms))
            if len(m_to_i.get(m, set())) == 1:
                resolved[d] = next(iter(m_to_i[m]))

        print("APPROVED_D_CODES", len(approved))
        print("RESOLVED_D_CODES", len(resolved))

        counts = Counter()
        selected = {}

        for fp in discover(args.dur_dir):
            counts["files"] += 1
            for row in objects(fp):
                counts["rows"] += 1

                if trim(row.get("TYPE_CODE")).upper() != "A":
                    continue

                da = trim(row.get("INGR_CODE"))
                db = trim(row.get("MIXTURE_INGR_CODE"))
                seq = trim(row.get("DUR_SEQ"))

                if not da or not db or not seq:
                    continue

                if da not in resolved or db not in resolved:
                    continue

                ia = resolved[da]
                ib = resolved[db]

                if ia == ib:
                    counts["same_ingredient"] += 1
                    continue

                a, b = sorted((ia, ib))
                d1, d2 = sorted((da, db))

                # Stage one representative per ingredient pair + DUR_SEQ.
                # This is a candidate, not proof that all formulations
                # and routes share an identical clinical restriction.
                key = (a, b, d1, d2, seq)
                counts["mapped_rows"] += 1

                if key in selected:
                    continue

                evidence = (
                    trim(row.get("INGR_KOR_NAME")) + " / " +
                    trim(row.get("MIXTURE_INGR_KOR_NAME"))
                ).strip(" /")

                selected[key] = {
                    "dur_seq": seq,
                    "dur_codes": [d1, d2],
                    "scope": "INGREDIENT_CANDIDATE",
                    "source_file": fp.name,
                    "representative_row": row,
                    "warning": (
                        "Candidate only; review source conditions, "
                        "formulation, route and ingredient equivalence."
                    ),
                    "evidence": evidence or f"{d1} / {d2}"
                }

        print("COUNTS", json.dumps(dict(counts), sort_keys=True))
        print("INGREDIENT_RULE_CANDIDATES", len(selected))

        if not args.apply:
            print("DRY_RUN_ONLY. RDS was not modified.")
            return

        if not selected:
            print("NO_ELIGIBLE_APPROVED_MAPPINGS. Nothing inserted.")
            return

        conn.execute(
            "SELECT set_config('lock_timeout', %s, true)",
            ("3s",)
        )
        conn.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (20261023,)
        )
        next_id = conn.execute("""
            SELECT COALESCE(MAX(interaction_rule_id), 0)
            FROM public.interaction_rule
        """).fetchone()[0]

        inserted = 0
        for (a, b, d1, d2, seq), info in sorted(selected.items()):
            digest = hashlib.sha256(
                json.dumps(
                    [a, b, d1, d2, seq],
                    ensure_ascii=False
                ).encode()
            ).hexdigest()[:24]
            source_id = f"{seq}:{digest}"

            exists = conn.execute("""
                SELECT 1 FROM public.interaction_rule
                WHERE source_dataset = %s
                  AND source_record_id = %s
                LIMIT 1
            """, (SOURCE, source_id)).fetchone()

            if exists:
                continue

            next_id += 1

            conn.execute("""
                INSERT INTO public.interaction_rule (
                    interaction_rule_id,
                    subject_type, subject_id,
                    object_type, object_id,
                    interaction_type, severity, action_type,
                    evidence_text, source_dataset,
                    source_record_id, review_status,
                    active, target_text
                )
                VALUES (
                    %s, 'INGREDIENT', %s,
                    'INGREDIENT', %s,
                    'DDI', 'CONTRAINDICATED',
                    'AVOID_COMBINATION',
                    %s, %s, %s, 'PENDING',
                    false, %s
                )
            """, (
                next_id, a, b, info["evidence"],
                SOURCE, source_id,
                json.dumps(info, ensure_ascii=False, default=str)
            ))
            inserted += 1

        conn.commit()
        print("INSERTED_PENDING_INACTIVE", inserted)
        print("No rules were automatically activated.")

if __name__ == "__main__":
    main()
