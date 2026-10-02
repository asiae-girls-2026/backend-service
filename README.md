# 케어퐁 backend-service

Windows 데스크톱 케어펫 **케어퐁(CarePong)** 의 서버. 1차 베타 10/7 → 2차 10/26.
구조: `api`(FastAPI 메인) · `model`(FastAPI 추론, DA 모델) · `collector`(입력 수집기, Electron 자식 프로세스) · `infra`(Docker Compose·Caddy).

## 폴더

```
backend-service/
├ api/           메인 API. app/routers/ 화면별 주소, app/services/policy.py 팝업 정책, tests/
├ model/         추론 서버. predict.py = 혜윤 제공 파일 그대로 (model_v1.json 내장). tests/validation_samples.json 5건으로 검증
├ collector/     pynput 전역 후킹 → 1분 피처 16개 → JSON stdout. PyInstaller 로 exe
├ infra/         docker-compose.yml(caddy·api·model·db), caddy/Caddyfile
└ docs/          api-changelog.md
```

## 켜기

```
docker compose -f infra/docker-compose.yml up --build
```

| 주소 | 뭐가 보이나 |
|---|---|
| http://localhost:8000/docs | Swagger. 직접 눌러보며 테스트 |
| http://localhost:8000/health | `{"ok": true}` |
| http://localhost:8001/health | 모델 컨테이너 (model_version·컷) |

Docker 없이: `cd api && pip install -r requirements.txt && uvicorn app.main:app --reload`
테스트: `cd api && pytest` / `cd model && pytest` / `cd collector && pytest`

## 데이터 흐름 (10/2 확정)

| 길 | 주기 | 주소 | 비고 |
|---|---|---|---|
| 감지 피처 16개 | **1분마다 실시간** (08:30~18:30, 활동 0 창 포함) | `POST /v1/windows` | 서버 추론 → `condition_score`·`prompt`·`prompt_type` 회신. 활동 0 창은 null |
| 팝업 응답·체크인 | 즉시 | `POST /v1/self-reports` | `window_id` 로 예측과 짝. yesno + 1~5 |
| 이벤트(채원 마커 표) | **매일 08:30 또는 앱 시작 시** 묶음 | `POST /v1/events/batch` | 로컬 적재 → `accepted_ids` 만 삭제. 일 집계는 서버가 산출 |
| 설정·펫·약 백업 | 바뀔 때 | `/v1/routines` `/v1/pet` `/v1/medications` | 원본은 앱 로컬 |

서버가 "띄울지" 판단하는 팝업은 **과부하 확인 하나**(혜윤 DA 명세 3_동작규칙: 과부하 1순위 · 고정 보충 11:30/14/15:30/17 · 1시간 텀 · 하루 5~8회, `services/policy.py`). 복약·물·스트레칭·휴식 알림과 정기 체크인은 앱이 띄우고 서버는 기록만 받는다. 완화 보상 상한(3회·30분)도 앱.

## 엔드포인트 — 1차 [Stub] 15

| 메서드 | 주소 | 용도 |
|---|---|---|
| GET | /health | 생존 |
| POST | /v1/enroll | 앱이 만든 CP 번호 등록 → 토큰 |
| POST | /v1/windows · /v1/windows/batch | 1분 창 → 추론 → 팝업 여부 (배치 ≤240, 팝업 없음) |
| POST | /v1/self-reports | 팝업 응답(예/아니오·1~5)·체크인 |
| POST | /v1/events/batch | 이벤트 묶음 (이벤트명은 채원 마커 표 고정) |
| GET/POST/PUT/DELETE | /v1/medications[/{id}] | 설정 안 약 등록 관리 |
| GET/PUT | /v1/routines | 알람 간격·방해금지(18:30~08:30)·펫 투명도 |
| GET/PUT | /v1/pet | 펫 백업 (캐릭터 슬롯·레벨·하트·방꾸미기) |
| GET | /v1/config | 정책값 (컷·상한·방해금지·간격 기본값) |

2차 [Planned] 12: `/pet/actions` `/medications/{id}/logs` `/routines/logs` `/relief-sessions`(이벤트로 대체) · `/usage/daily`(GET, 서버 집계) · `/drugs/search` `/supplements/search` · `/telegram/*` · `/ppg/*`

## AWS 로 옮길 때 (10/4)

1. EC2(t3, Ubuntu)에 Docker 설치, 저장소 clone
2. compose: `db` 상자 삭제 → `DATABASE_URL` 을 RDS 주소로. `model`·`db` 의 `ports` 삭제, `api` 의 `command`(--reload) 삭제
3. `Caddyfile` 첫 줄 `:80` → 도메인 (HTTPS 자동)
4. `.env` 에 `DATA_GO_KR_KEY`(2차) — 저장소에 올리지 않는다
5. 앱의 `CAREPONG_API` 를 그 주소로

## 규칙

- 앱이 보내는 기록엔 전부 `client_event_id`. 재전송돼도 한 번만 기록
- 시각은 앱 시계(KST, ISO)로. 서버는 받은 시각을 따로 적는다
- 무응답(`timeout`)을 미복용으로 치지 않는다
- 주소·필드·이벤트명 바꾸려면 팀에 말하고 같이 바꾼다 (채원 마커 표·대시보드가 이름을 본다)
- 원시 키 코드·텍스트·창 제목은 어디에도 넣지 않는다
- 팝업 상태가 메모리에 있으므로 DB 이관 전엔 **워커 1개, 배포는 08:30 전**

## 아직 결정 안 된 것

- 원시(raw) 보관: 혜윤 DB설계 = S3 저장 + `windows.raw_url` 포인터 (1차~2차). 수집기가 원시를 어디까지 남길지 1차 후
- D07 인증 방식 (지금은 임시 `X-User-Id` 헤더 — 실데이터 수집 전 교체)
- Q07 명명(스트레스→컨디션), Q08 팝업 문구
