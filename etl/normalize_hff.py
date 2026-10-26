import os
import re
import unicodedata
from collections import defaultdict

import psycopg


def normalize_name(value):
    # 유니코드와 공백 표기만 통일
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\u200b", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def main():
    groups = defaultdict(list)
    total_rows = 0

    with psycopg.connect(
        os.environ["DATABASE_URL"],
        connect_timeout=10
    ) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '120s'")

        database = conn.execute(
            "SELECT current_database()"
        ).fetchone()[0]

        if database != "carepong_beta":
            raise RuntimeError("잘못된 DB: " + database)

        rows = conn.execute("""
            SELECT raw_ingredient_name, COUNT(*)
            FROM public.hff_product_ingredient
            GROUP BY raw_ingredient_name
        """).fetchall()

    for raw_name, count in rows:
        normalized = normalize_name(raw_name)
        if not normalized:
            continue

        groups[normalized].append((raw_name, count))
        total_rows += count

    merged = {
        name: variants
        for name, variants in groups.items()
        if len(variants) > 1
    }

    affected = sum(
        count
        for variants in merged.values()
        for _, count in variants
    )

    print("========== 정규화 결과 ==========")
    print("전체 원료 후보:", total_rows)
    print("기존 고유 문자열:", len(rows))
    print("정규화 후 고유 문자열:", len(groups))
    print("표기가 통합된 그룹:", len(merged))
    print("통합 그룹에 속한 원료 행:", affected)

    print("\n========== 통합 후보 예시 ==========")

    ranked = sorted(
        merged.items(),
        key=lambda item: sum(
            count for _, count in item[1]
        ),
        reverse=True
    )

    for name, variants in ranked[:20]:
        print("\n정규화 키:", name[:120])

        for raw, count in sorted(
            variants,
            key=lambda item: item[1],
            reverse=True
        )[:5]:
            print(f"  {count:,}건 | {raw[:120]}")

    print("\n========== 검수 필요 유형 ==========")

    flags = {
        "괄호 포함": 0,
        "함량/비율 표기": 0,
        "혼합/복합 제제": 0,
    }

    for name, variants in groups.items():
        count = sum(n for _, n in variants)

        if "(" in name or ")" in name:
            flags["괄호 포함"] += count

        if "%" in name or re.search(r"\d+\s*(mg|g|μg)", name, re.I):
            flags["함량/비율 표기"] += count

        if any(word in name for word in ("혼합", "복합", "제제")):
            flags["혼합/복합 제제"] += count

    for label, count in flags.items():
        print(f"{label}: {count:,}건")

    print("\nDRY-RUN 완료: RDS 변경 없음")
    print("표준 성분 자동 승인: 0건")


if __name__ == "__main__":
    main()
