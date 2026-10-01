# backend-service

데스크톱 케어펫 서비스의 백엔드. 키보드·마우스 입력 리듬으로 업무 컨디션을 감지하고, 복약·루틴 관리와 펫 육성 기록을 다룬다.

## 구성

```
api/      FastAPI — 클라이언트 API (복약·루틴·펫·완화·감지·이용지표)
model/    FastAPI — 컨디션 모델 추론 (/predict). api가 내부 호출
infra/    docker-compose, Caddy (HTTPS 리버스 프록시)
docs/     API 변경 기록
```

```
[Electron 클라이언트] ──HTTPS──▶ caddy ──▶ api ──▶ model
                                          └──▶ PostgreSQL (로컬: 컨테이너 / 운영: AWS RDS)
```

- 추론은 서버에서 한다. 클라이언트는 1분 창 피처를 올리고, `prompt` 응답으로 팝업 여부를 받는다.
- 게임·일기 등 내용 데이터는 클라이언트 로컬에만 둔다. 서버에는 숫자 지표만 올린다.

## 실행

```bash
docker compose -f infra/docker-compose.yml up --build
```

| 주소 | 용도 |
|---|---|
| http://localhost:8000/docs | API 명세 (Swagger) |
| http://localhost:8000/health | api 생존 확인 |
| http://localhost:8001/health | model 생존 확인 |

Docker 없이 api만:

```bash
cd api && pip install -r requirements.txt && uvicorn app.main:app --reload
```

## 엔드포인트

| 영역 | 메서드 | 주소 | 상태 |
|---|---|---|---|
| 운영 | GET | /health | Stub |
| 운영 | POST | /v1/events/batch | Stub |
| 회원 | POST | /v1/enroll | Stub |
| 복약 | GET / POST / DELETE | /v1/medications | Stub |
| 복약 | POST | /v1/medications/{id}/logs | Stub |
| 루틴 | GET / PUT | /v1/routines | Stub |
| 루틴 | POST | /v1/routines/logs | Stub |
| 펫 | GET / PUT | /v1/pet | Stub |
| 펫 | POST | /v1/pet/actions | Stub |
| 완화 | POST | /v1/relief-sessions | Stub |
| 약 정보 | GET | /v1/drugs/search | Stub |
| 감지 | POST | /v1/windows, /v1/windows/batch | Stub |
| 감지 | POST | /v1/self-reports | Stub |
| 이용 지표 | PUT | /v1/usage/daily | Draft |
| 설정 | GET | /v1/config | Draft |
| 텔레그램 | POST | /v1/telegram/pairings, /webhook | Draft |
| PPG | POST / GET | /v1/ppg/* | Planned |

상태: **Stub** 1.0 범위, 목 응답 · **Draft** 담당자 확정 대기 · **Planned** 정식 배포

## 규칙

- 클라이언트가 보내는 기록은 `client_event_id`로 멱등 처리한다. 재전송돼도 한 번만 기록.
- 발생 시각(`occurred_at`)과 서버 수신 시각을 분리한다.
- 무응답(`timeout`)을 미복용으로 간주하지 않는다.
- 외부 API 키·DB 접속 정보는 환경변수로만. 저장소에 올리지 않는다.
- 주소·필드 변경은 `docs/api-changelog.md`에 남긴다.

## 배포 (AWS)

EC2 1대 (Docker Compose) + RDS PostgreSQL 1대. `infra/docker-compose.yml`에서 `db` 서비스를 제거하고 `DATABASE_URL`을 RDS로 바꾼 뒤 같은 명령으로 올린다. Caddyfile 첫 줄을 도메인으로 바꾸면 HTTPS 인증서가 자동 발급된다.
