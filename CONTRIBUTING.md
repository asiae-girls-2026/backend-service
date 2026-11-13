# Contributing

Thanks for your interest in CarePong. This project was built by a five-person student team and is published as-is for learning and reuse. Issues and pull requests are welcome.

## Before you start

- Read [README.md](README.md) and [docs/architecture.md](docs/architecture.md).
- Check open issues to avoid duplicate work. For larger changes, open an issue first.

## Development

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml up --build   # api :8000, model :8001, postgres
cd api && pip install -r requirements.txt pytest && pytest
cd model && pytest
```

## Pull requests

- One topic per PR. Keep the diff small enough to review in one sitting.
- Run `pytest` in `api/` and `model/`. CI runs the same tests on every PR.
- API paths, field names and event names are shared with the desktop app. If you change one, say so in the PR and add a line to [docs/api-changelog.md](docs/api-changelog.md).
- Never commit secrets, real user identifiers, or exported data. `.env` is ignored; `.env.example` lists every variable.
- Folders `etl/`, `lambda/`, `deploy/` and `infra/ddi-policy/` hold the data pipeline and DDI (drug–drug interaction) rules. Changes there need a review from the data maintainer.

## Commit messages

Use a short imperative subject (`feat: …`, `fix: …`, `docs: …`). Korean or English are both fine.

---

# 기여 안내 (한국어)

케어퐁은 5인 학생 팀 프로젝트를 학습·재사용 목적으로 공개한 저장소입니다. 이슈와 PR 을 환영합니다.

- 시작 전 [README.ko.md](README.ko.md) 와 [docs/architecture.md](docs/architecture.md) 를 읽어 주세요. 큰 변경은 이슈를 먼저 열어 주세요.
- 로컬 실행·테스트는 위 Development 절과 같습니다.
- PR 은 주제 하나씩. `api/`·`model/` 에서 `pytest` 통과. API 주소·필드·이벤트명은 데스크톱 앱과 공유되니 바꾸면 PR 에 적고 `docs/api-changelog.md` 에 한 줄 남겨 주세요.
- 비밀값·실제 사용자 식별자·내보낸 데이터는 커밋하지 않습니다.
- `etl/`·`lambda/`·`deploy/`·`infra/ddi-policy/` 는 데이터 파이프라인·DDI 규칙 영역으로, 데이터 담당 리뷰가 필요합니다.
