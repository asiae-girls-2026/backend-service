# 케어퐁 서버 뼈대 (목 응답)

화면기획서 v0.5 기준. PPG 제외. ML은 `/v1/config` 껍데기만.
모든 응답은 가짜 고정값 — **모양(필드 이름·타입)만 팀 합의 대상**이고, 안쪽 로직(DB 저장)은 나중에 채운다.

## 폴더

```
carepong-server/
├ docker-compose.yml   상자 4개(caddy·api·model·db)를 한 번에 켜는 설정
├ caddy/Caddyfile      문지기. AWS에선 도메인 넣으면 HTTPS 자동
├ api/                 메인 API (FastAPI)
│  └ app/routers/      화면별 엔드포인트 — enroll·medications·routines·pet·relief·drugs·events·config
├ model/               모델 API 껍데기. DA 모델 오면 main.py의 /predict만 채움
└ DA_요청서_모델API.md  혜윤한테 보낼 요청서
```

## 1단계 — 내 PC에서 켜기

Docker Desktop 설치 후:

```
docker compose up --build
```

켜지면:

| 주소 | 뭐가 보이나 |
|---|---|
| http://localhost:8000/docs | **Swagger 화면** (멘토가 보여준 그 페이지). 여기서 직접 눌러보며 테스트 |
| http://localhost:8000/health | `{"ok": true}` 나오면 서버 살아있음 |
| http://localhost:8001/health | 모델 컨테이너 살아있음 |

Docker 없이 API만 빨리 보려면:

```
cd api
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## 2단계 — Electron 앱에서 호출

프로토타입 `out/main/index.js`에 이미 `fetch`가 있으니 같은 방식으로:

```js
const BASE = process.env.CAREPONG_API ?? "http://localhost:8000";
const res = await fetch(`${BASE}/health`);
console.log(await res.json());   // {ok: true, ...}
```

이게 되면 "앱 ↔ 서버 첫 연결" 완료. 그다음 약 등록(`POST /v1/medications`)부터 하나씩 붙인다.

## 3단계 — AWS로 옮기기 (다음 주)

1. EC2(t3, Ubuntu)에 Docker 설치, 이 폴더 통째로 복사
2. `docker-compose.yml`에서 `db` 상자를 지우고 `DATABASE_URL`을 RDS 주소로
3. `caddy/Caddyfile` 첫 줄 `:80` → 도메인으로 (HTTPS 자동)
4. 같은 명령 `docker compose up -d --build`
5. 앱의 `CAREPONG_API`만 그 주소로 바꿈

## 엔드포인트 (화면 → API)

| 화면 | 메서드 | 주소 | 용도 |
|---|---|---|---|
| M07 회원번호 | POST | /v1/enroll | CP 번호 등록 → 토큰 |
| M02~M04·M07 | GET / POST / DELETE | /v1/medications | 약 목록·등록·삭제 |
| P02 약 알람 | POST | /v1/medications/{id}/logs | 먹었어요·나중에·무응답 |
| M07 설정 | GET / PUT | /v1/routines | 물·스트레칭·휴식·방해금지 |
| P02 물·스트레칭·휴식 | POST | /v1/routines/logs | 이행 기록 |
| P01·P06·P07 | GET / PUT | /v1/pet | 펫 상태 백업 |
| P01·P07 | POST | /v1/pet/actions | 밥·쓰다듬기·산책·환생 |
| P04·P05 | POST | /v1/relief-sessions | 심호흡·명상 완료/중단 |
| M04 약 물어보기 | GET | /v1/drugs/search?q= | e약은요 프록시 (키를 앱에 안 둠) |
| 운영 | POST | /v1/events/batch | 수집 멈춤·에러 |
| 운영 | GET | /health | 생존 확인 |
| 이용 지표 | PUT | /v1/usage/daily | 게임·대화·알람 숫자만 (일 1건) · [Draft] |
| P03 감지 | POST | /v1/windows | 1분 창 업로드 → **서버 추론** → 지수·팝업 여부 회신 |
| P03 감지 | POST | /v1/self-reports | 과부하 확인 예/아니오·정기 체크인 (정답지) |
| 모델 설정 | GET | /v1/config | 컷·상한·체크인 시각 (가중치 없음) · [Draft] |
| 텔레그램 | POST | /v1/telegram/pairings, /webhook | [Draft] 1.0 포함 미정 |
| PPG | POST/GET | /v1/ppg/* | [Planned] 정식 배포 |

**서버에 안 보내는 것**: 일기 원문, 기분 텍스트, 펫 이름 등 내용 데이터. 게임은 로컬 로그만 남기고 **숫자 지표만** `/usage/daily`로.

**상태 표기**: summary 접두어 `[Stub]`(1.0, 모양 확정 대상) / `[Draft]`(담당자 확정 대기) / `[Planned]`(정식 배포). Swagger 태그 설명에도 같은 내용.
**추론 위치 (D03, 10/2 확정)**: 서버. 앱은 피처만 올리고 서버 응답의 `prompt`로 팝업을 띄운다. `model/` 컨테이너의 `/predict`가 실제 경로 — 혜윤 `predict.py`로 교체.

## 규칙 (지금부터 지키면 나중에 안 터짐)

- 앱이 보내는 기록엔 전부 `client_event_id`를 넣는다. 재전송돼도 서버가 한 번만 기록
- 시각은 `occurred_at`(앱에서 일어난 시각)으로 보내고, 서버는 받은 시각을 따로 적는다
- 무응답(`timeout`)을 미복용으로 치지 않는다
- 주소·필드 이름 바꾸려면 팀에 말하고 같이 바꾼다 (`/v1/`이 붙은 이유)
