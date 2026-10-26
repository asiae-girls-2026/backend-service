import os
import json
import urllib.parse
import urllib.request
import urllib.error
from carepong_secrets import get_api_key

APIS = {
    "MED": (
        "https://apis.data.go.kr/1471000/"
        "DrugPrdtPrmsnInfoService08/"
        "getDrugPrdtPrmsnInq08",
        "MFDS_MED_KEY"
    ),
    "DUR": (
        "https://apis.data.go.kr/1471000/"
        "DURPrdlstInfoService03/"
        "getUsjntTabooInfoList03",
        "MFDS_DUR_KEY"
    )
}

for name, (endpoint, env_name) in APIS.items():
    print(f"\n========== {name} ==========", flush=True)

    key = urllib.parse.unquote(get_api_key(env_name).strip())
    if not key:
        raise SystemExit(f"{env_name}가 없습니다.")

    params = {
        "serviceKey": key,
        "pageNo": 1,
        "numOfRows": 5,
        "type": "json"
    }

    url = endpoint + "?" + urllib.parse.urlencode(params)

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "CarePong-ETL/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = response.read()
            print("HTTP:", response.status)

    except urllib.error.HTTPError as exc:
        print("HTTP 오류:", exc.code)
        body = exc.read().decode("utf-8", errors="replace")

        known_errors = [
            "SERVICE_ACCESS_DENIED_ERROR",
            "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
            "NO_OPENAPI_SERVICE_ERROR",
            "PERMISSION_DENIED",
            "INVALID_REQUEST_PARAMETER_ERROR",
            "SERVICE_KEY_IS_NULL",
            "INVALID_REQUEST_PARAMETER_ERROR",
            "DEADLINE_HAS_EXPIRED_ERROR",
            "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
            "Forbidden",
            "Access Denied",
        ]

        matched = [e for e in known_errors if e in body]

        print("오류 유형:", matched or "기타 오류")
        print("응답 형식:", exc.headers.get("Content-Type"))
        continue
    except Exception as exc:
        print("연결 오류:", type(exc).__name__)
        continue

    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        print("JSON이 아닌 응답")
        print("응답 시작:", payload[:200].decode(
            "utf-8", errors="replace"
        ))
        continue

    header = data.get("response", data).get("header", {})
    body = data.get("response", data).get("body", {})

    print("응답 코드:", header.get("resultCode"))
    print("응답 메시지:", header.get("resultMsg"))

    if not isinstance(body, dict):
        print("응답 구조:", str(data)[:300])
        continue

    print("전체 건수:", body.get("totalCount"))

    items = body.get("items", [])
    if isinstance(items, dict):
        items = items.get("item", items)

    if isinstance(items, dict):
        items = [items]
    if items is None:
        items = []

    print("조회 건수:", len(items))

    for i, item in enumerate(items[:2], 1):
        if isinstance(item, dict):
            print(f"\n레코드 {i} 필드:")
            print(", ".join(item.keys()))

            for field, value in item.items():
                if any(word in field.upper() for word in (
                    "ITEM_SEQ", "INGR", "MCPN", "CODE",
                    "TYPE", "PROHBT", "REMARK"
                )):
                    print(f"  {field}: {str(value)[:120]}")
