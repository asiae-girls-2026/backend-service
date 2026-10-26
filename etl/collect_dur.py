import gzip
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from carepong_secrets import get_api_key

BASE = (
    "https://apis.data.go.kr/1471000/"
    "DURPrdlstInfoService03/"
    "getUsjntTabooInfoList03"
)

OUT = Path(
    "/home/ubuntu/backend-service/etl/data/dur"
)

PAGE_SIZE = 500
OUT.mkdir(parents=True, exist_ok=True)
os.chmod(OUT, 0o700)

key = urllib.parse.unquote(
    get_api_key("MFDS_DUR_KEY").strip()
)


def parse(data, page):
    root = data.get("response", data)
    header = root.get("header", {})
    body = root.get("body", {})

    code = str(header.get("resultCode", ""))
    if code not in ("00", "0"):
        msg = str(header.get("resultMsg", ""))
        raise RuntimeError(
            f"API 오류: code={code}, message={msg[:180]}"
        )

    total = int(body["totalCount"])
    page_no = int(body["pageNo"])

    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]

    if not isinstance(items, list):
        raise RuntimeError("items 구조 오류")

    if page_no != page:
        raise RuntimeError(
            f"페이지 불일치: 요청={page}, 응답={page_no}"
        )

    return total, items


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
                req, timeout=60
            ) as response:
                data = json.load(response)

            parse(data, page)
            return data

        except urllib.error.HTTPError as exc:
            error = f"HTTP {exc.code}"
            if exc.code in (400, 401, 403):
                raise RuntimeError(error) from None

        except Exception as exc:
            # 인증키가 포함된 요청 URL은 출력하지 않음
            error = (
                f"{type(exc).__name__}: {str(exc)[:180]}"
                if not isinstance(exc, urllib.error.URLError)
                else type(exc).__name__
            )

        if attempt < 2:
            time.sleep(3 * (attempt + 1))

    raise RuntimeError(
        f"페이지 {page} 수집 실패: {error}"
    )


first = fetch(1)
total, first_items = parse(first, 1)
pages = math.ceil(total / PAGE_SIZE)

print("원천 총건수:", total, flush=True)
print("예상 페이지:", pages, flush=True)

if total <= 0:
    raise SystemExit("데이터가 없습니다.")

limit = int(sys.argv[1]) if len(sys.argv) > 1 else pages
limit = min(limit, pages)

processed = 0

for page in range(1, limit + 1):
    target = OUT / f"page_{page:05d}.json.gz"

    if target.exists():
        with gzip.open(target, "rt", encoding="utf-8") as f:
            data = json.load(f)
        status = "SKIP"
    else:
        data = first if page == 1 else fetch(page)
        status = "SAVE"

    saved_total, items = parse(data, page)

    expected = min(
        PAGE_SIZE,
        total - (page - 1) * PAGE_SIZE
    )

    if saved_total != total or len(items) != expected:
        raise RuntimeError(
            f"페이지 {page} 데이터 건수 불일치: "
            f"{len(items)}/{expected}, "
            f"total={saved_total}/{total}"
        )

    if status == "SAVE":
        temp = target.with_suffix(".tmp")
        with gzip.open(temp, "wt", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(temp, target)

    processed += len(items)

    print(
        f"[{page}/{limit}] {status} "
        f"{len(items)}건 / 누적 {processed}건",
        flush=True
    )

    if status == "SAVE":
        time.sleep(0.2)

print("수집 처리 완료:", processed, "건")
print("저장 경로:", OUT)
