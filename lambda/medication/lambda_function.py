import json
import os
from functools import lru_cache

import boto3
import psycopg
from psycopg.conninfo import make_conninfo

from ddi_operational import check_medications


@lru_cache(maxsize=1)
def get_db_secret():
    # DB 비밀번호는 환경변수나 코드에 직접 넣지 않음.
    # Lambda 환경변수 DB_SECRET_ARN을 이용해 Secrets Manager에서 조회.
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(
        SecretId=os.environ["DB_SECRET_ARN"]
    )
    return json.loads(response["SecretString"])


def connect_db():
    secret = get_db_secret()

    # [중요] DB_HOST = 기존 RDS의 엔드포인트 호스트명
    # RDS Proxy 주소가 아님. postgresql:// 접두사도 넣지 않음.
    conninfo = make_conninfo(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "carepong_beta"),
        user=secret["username"],
        password=secret["password"],
        sslmode="require",
        connect_timeout=5,
        options="-c default_transaction_read_only=on",
    )

    return psycopg.connect(
        conninfo,
        autocommit=True
    )


def handler(event, context):
    # 초기 단계에서는 DDI 검사만 수행.
    # Bedrock Tool Use는 Lambda DDI 테스트 성공 후 연결.

    mode = event.get("mode", "ddi_check")

    if mode == "ddi_multi_check":
        from ddi_multi import check_multi
        return check_multi(event, handler)

    if mode != "ddi_check":
        raise ValueError("현재는 ddi_check 모드만 지원합니다.")

    product_a = int(event["product_a"])
    product_b = int(event["product_b"])

    if product_a <= 0 or product_b <= 0:
        raise ValueError("제품 ID는 양의 정수여야 합니다.")

    if product_a == product_b:
        raise ValueError("서로 다른 제품 ID를 입력하세요.")

    with connect_db() as conn:
        result = check_medications(
            conn,
            product_a,
            product_b
        )

    return result
