import gzip
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from carepong_secrets import get_api_key

BASE = (
    "https://apis.data.go.kr/1471000/"
    "DrugPrdtPrmsnInfoService08/"
    "getDrugPrdtMcpnDtlInq08"
)

OUT = Path(
    "/home/ubuntu/backend-service/etl/data/med_ingredients"
)
PAGE_SIZE = 500

OUT.mkdir(parents=True, exist_ok=True)
os.chmod(OUT, 0o700)

key = urllib.parse.unquote(
    get_api_key("MFDS_MED_KEY").strip()
)


def fetch(page):
    params = {
        "serviceKey": key,
        "pageNo": page,
        "numOfRows": PAGE_SIZE,
        "type": "json",
    }

    url = BASE + "?" + urllib.parse.urlencode(params)

    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "CarePong-ETL/1.0"},
            )

            with urllib.request.urlopen(
                req, timeout=40
            ) as resp:
                data = json.load(resp)

            if data.get("header", {}).get("resultCode") != "00":
                raise ValueError("API 응답 코드 비정상")

            return data

        except Exception:
            if attempt == 2:
                raise RuntimeError(
                    f"페이지 {page} 수집 실패"
                ) from None
            time.sleep(3 * (attempt + 1))


first = fetch(1)
total = int(first["body"]["totalCount"])
pages = math.ceil(total / PAGE_SIZE)

print("원천 총건수:", total, flush=True)
print("예상 페이지:", pages, flush=True)

if total <= 0:
    raise SystemExit("수집 대상이 없습니다.")

limit = int(sys.argv[1]) if len(sys.argv) > 1 else pages
limit = min(limit, pages)

collected = 0

for page in range(1, limit + 1):
    target = OUT / f"page_{page:05d}.json.gz"

    if target.exists():
        with gzip.open(target, "rt", encoding="utf-8") as f:
            saved = json.load(f)
        data = saved
        status = "SKIP"
    else:
        data = first if page == 1 else fetch(page)
        status = "SAVE"

    body = data["body"]
    items = body.get("items") or []

    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]

    expected = min(PAGE_SIZE, total - (page - 1) * PAGE_SIZE)

    if (
        int(body.get("totalCount", -1)) != total
        or int(body.get("pageNo", -1)) != page
        or len(items) != expected
    ):
        raise RuntimeError(
            f"페이지 {page}: 응답 건수 또는 페이지 정보 불일치"
        )

    if status == "SAVE":
        temp = target.with_suffix(".tmp")
        with gzip.open(temp, "wt", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(temp, target)

    collected += len(items)

    print(
        f"[{page}/{limit}] {status} "
        f"{len(items)}건 / 누적 {collected}건",
        flush=True,
    )

    if status == "SAVE":
        time.sleep(0.2)

print("수집 처리 완료:", collected, "건")
print("저장 경로:", OUT)
