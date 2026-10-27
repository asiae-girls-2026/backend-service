# 복약 2차 운영 API

모든 사용자 API는 `Authorization: Bearer <access_token>`이 필요하다. 요일은 `0=월요일`부터 `6=일요일`, 기본 시간대는 `Asia/Seoul`이다.

## 등록 및 일정

### POST `/v1/users/me/medications`

헤더 `Idempotency-Key` 또는 본문의 `client_medication_id` 중 하나가 필요하다. 동일 키와 동일 요청은 기존 결과를 반환하고, 내용이 다르면 `409`를 반환한다.

```json
{
  "item_type": "DRUG",
  "product_id": 326,
  "client_medication_id": "local-med-001",
  "schedule": {
    "times": ["09:00", "20:00"],
    "repeat_days": [0, 1, 2, 3, 4, 5, 6],
    "timezone": "Asia/Seoul"
  },
  "start_date": "2026-10-27",
  "end_date": null,
  "dose_text": "1정"
}
```

기존 Electron의 최상위 `times`, `repeat_days` 형식도 계속 받는다.

### PATCH `/v1/users/me/medications/{id}`

`schedule_revision`은 마지막 목록 조회값이다. 불일치하면 `409 SCHEDULE_REVISION_CONFLICT`가 발생한다. 변경 전 과거 회차는 보존하고, 현재 이후의 `SCHEDULED` 회차만 새 일정으로 다시 생성한다.

```json
{
  "schedule_revision": 1,
  "schedule": {
    "times": ["08:30"],
    "repeat_days": [0, 2, 4],
    "timezone": "Asia/Seoul"
  }
}
```

### DELETE `/v1/users/me/medications/{id}`

행을 삭제하지 않고 `active=false`로 전환한다. 과거 이력은 유지하고 미래 `SCHEDULED` 회차만 제거한다.

## 예정 회차와 복용기록

### GET `/v1/users/me/medications/today?date=2026-10-27&timezone=Asia/Seoul`

해당 로컬 날짜의 회차와 `scheduled/taken/missed/unknown/pending` 합계를 반환한다.

### POST `/v1/users/me/medications/{id}/intakes`

```json
{
  "occurrence_id": 1001,
  "event_type": "TAKEN",
  "client_event_id": "intake-001",
  "occurred_at": "2026-10-27T09:02:00+09:00"
}
```

`SNOOZED`는 행동 이력만 남기고 회차 상태는 바꾸지 않는다. `TAKEN`과 사용자 보고 `MISSED`만 최종 상태를 변경한다. 동일 `client_event_id` 재전송은 중복 저장하지 않는다.

### POST `/v1/users/me/medication-intakes/batch`

오프라인 큐 재전송용이다. 최대 500건을 받고 `accepted_ids`, `rejected_ids`를 반환한다.

## 리포트

### GET `/v1/users/me/medication-adherence?from=2026-10-01&to=2026-10-31&timezone=Asia/Seoul`

전체 및 일별 `scheduled`, `taken`, `missed`, `unknown`, `pending`, `snoozed_actions`를 반환한다. 복약률은 `TAKEN / (TAKEN + MISSED)`이며 `UNKNOWN`과 아직 판정되지 않은 `SCHEDULED`는 분모에서 제외한다.

## 서버 회차 생성

API 조회 때 필요한 회차를 멱등 생성하며, 앱이 꺼져 있어도 다음 작업을 매일 실행해 미래 30일 회차를 유지한다.

```bash
python -m app.jobs.generate_medication_occurrences --days 30
```

자동으로 `MISSED`를 확정하지 않는다. 알림 45초 무응답이나 앱 미실행만으로 실제 미복용을 단정하지 않기 때문이다.

## 데이터 갱신

`etl/refresh_pipeline.py`는 `med-ingredients`, `hff-ingredients`, `dur-raw` 수집을 실행하고 `sync_run`에 성공·실패 및 건수를 기록한다. `dur-raw`는 원문 수집만 하며 DDI 규칙 승인·활성화를 수행하지 않는다.

```bash
python etl/refresh_pipeline.py --dataset all --pages 1
python etl/refresh_pipeline.py --dataset all --refresh --apply
```

Prefect 3 실행 진입점은 `etl/prefect_flows.py`다. 기본 스케줄은 매주 일요일 03:00 KST이며 환경변수 `CAREPONG_REFRESH_CRON`으로 변경할 수 있다.
