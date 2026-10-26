import argparse
import json
import sys
import urllib.request
import time
from collections import Counter

import psycopg

API_BASE = "https://openapi.foodsafetykorea.go.kr/api"
PAGE_SIZE = 1000


def split_ingredients(raw):
    """괄호 내부의 쉼표는 보존하고 최상위 쉼표만 분리한다."""
    result, buf, stack = [], [], []
    pairs = {"(": ")", "[": "]", "{": "}"}

    for ch in raw:
        if ch in pairs:
            stack.append(pairs[ch])
        elif stack and ch == stack[-1]:
            stack.pop()

        if ch == "," and not stack:
            value = "".join(buf).strip()
            if value:
                result.append(value)
            buf = []
        else:
            buf.append(ch)

    value = "".join(buf).strip()
    if value:
        result.append(value)
    return result


def fetch_page(key, start, end):
    url = f"{API_BASE}/{key}/C003/json/{start}/{end}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "CarePong-ETL/1.0"}
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as resp:
                body = json.load(resp)

            section = body.get("C003")
            if not isinstance(section, dict):
                raise RuntimeError("C003 응답 형식 오류")

            result = section.get("RESULT") or {}
            if result.get("CODE") != "INFO-000":
                raise RuntimeError(f"C003 API 오류: {result}")

            rows = section.get("row", [])
            if not isinstance(rows, list):
                raise RuntimeError("C003 row 형식 오류")

            return int(section["total_count"]), rows

        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def insert_batch(conn, records):
    if not records:
        return 0

    payload = json.dumps(records, ensure_ascii=False)

    row = conn.execute("""
        WITH incoming AS (
            SELECT DISTINCT
                hff_product_id,
                raw_ingredient_name
            FROM jsonb_to_recordset(%s::jsonb)
            AS x(
                hff_product_id bigint,
                raw_ingredient_name text
            )
        ),
        inserted AS (
            INSERT INTO public.hff_product_ingredient (
                hff_product_id,
                raw_ingredient_name,
                normalized_name,
                mapping_method,
                mapping_status
            )
            SELECT
                i.hff_product_id,
                i.raw_ingredient_name,
                NULL,
                NULL,
                'REVIEW'
            FROM incoming i
            ON CONFLICT (
                hff_product_id,
                (md5(raw_ingredient_name))
                  ) DO NOTHING
            RETURNING hff_product_ingredient_id
        )
        SELECT COUNT(*) FROM inserted
    """, (payload,)).fetchone()

    return row[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.pages < 0:
        parser.error("--pages는 0 이상이어야 합니다.")

    # 키는 표준입력으로만 받고 파일·로그에 저장하지 않음.
    key = sys.stdin.readline().strip()
    if not key:
        raise RuntimeError("API 인증키가 입력되지 않았습니다.")

    import os
    url = os.environ["DATABASE_URL"]

    counters = Counter()
    unmatched_examples = []
    pending = []

    with psycopg.connect(url, connect_timeout=10) as conn:
        database = conn.execute(
            "SELECT current_database()"
        ).fetchone()[0]

        if database != "carepong_beta":
            raise RuntimeError(f"잘못된 DB: {database}")

        products = dict(conn.execute("""
            SELECT prdlst_report_no, hff_product_id
            FROM public.hff_product
        """).fetchall())

        # 조회 시점의 제품 목록을 메모리에 보관
        conn.rollback()

        start = 1
        page = 0
        total = None

        while True:
            end = start + PAGE_SIZE - 1
            if total is not None:
                end = min(end, total)

            total, rows = fetch_page(key, start, end)
            if not rows:
                raise RuntimeError(
                    f"예상하지 않은 빈 페이지: {start}-{end}"
                )

            page += 1

            for row in rows:
                counters["received"] += 1

                report_no = str(
                    row.get("PRDLST_REPORT_NO") or ""
                ).strip()

                product_id = products.get(report_no)

                if product_id is None:
                    counters["unmatched"] += 1
                    if len(unmatched_examples) < 5:
                        unmatched_examples.append(report_no)
                    continue

                counters["matched"] += 1

                raw = str(row.get("RAWMTRL_NM") or "").strip()

                if not raw:
                    counters["empty_raw"] += 1
                    continue

                seen = set()
                for ingredient in split_ingredients(raw):
                    if ingredient in seen:
                        continue
                    seen.add(ingredient)


                    counters["review_candidates"] += 1
                    pending.append({
                        "hff_product_id": product_id,
                        "raw_ingredient_name": ingredient
                    })

            # 검토 모드에서는 쓰기 작업을 하지 않음
            if args.apply and pending:
                with conn.transaction():
                    counters["inserted"] += insert_batch(
                        conn, pending
                    )

            pending.clear()
            print(
                f"페이지 {page}: {start}-{end} / 총 {total:,}건",
                flush=True
            )

            if start + len(rows) > total:
                break

            if args.pages and page >= args.pages:
                break

            start += PAGE_SIZE

        if not args.apply:
            print("\nDRY-RUN: DB INSERT 없음")

        print("\n========== C003 수집 결과 ==========")
        for name, value in sorted(counters.items()):
            print(f"{name}: {value:,}")

        print("기존 제품 식별키 불일치 예시:", unmatched_examples)

        if counters["oversized"]:
            print(
                "주의: 500자를 초과한 원료 항목은 적재하지 않았습니다."
            )

        if args.apply:
            print("적재 상태: REVIEW (검수 전)")
        else:
            print("적재 상태: 미적재")


if __name__ == "__main__":
    main()
