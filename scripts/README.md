# scripts/ — 운영 점검·배포 스크립트 (민서)

`*_paste.txt` 는 **EC2 콘솔(또는 SSH)에 한 줄 전체를 붙여넣는** 용도다. 메모장으로 열어 Ctrl+A → Ctrl+C. dash 셸 기준(배시 전용 문법 없음), api 컨테이너 이름 `infra-api-1`.
`*.py` 는 로컬 또는 컨테이너 안에서 `python` 으로 실행. 전부 **읽기만** 한다(배포 스크립트 제외).

| 파일 | 어디서 | 하는 일 |
|---|---|---|
| `smoke.py <ALB 주소>` | 로컬 (표준 라이브러리만) | 헬스·설정·등록·혜윤 샘플 추론(74.1)·활동0·멱등·라벨·이벤트 배치 8개 검사 |
| `inspect_ec2_git_paste.txt` | EC2 | EC2 체크아웃 ↔ origin/main 커밋 차이, 변경 파일 |
| `deploy_api_paste.txt` | EC2 | `git merge origin/main` → api 컨테이너 재빌드 → `/health`·로그. 실패 시 `merge --abort` |
| `deploy_api_with_model_check_paste.txt` | EC2 | 위 + 개인화 훅 모듈 로드·모델 `/health`(standardized_input) 확인 |
| `verify_kbd_hook_paste.txt` | EC2 | 최근 창의 `model_version` 분포(`v1` vs `v1+kbd`)·모델 health |
| `kbd_score_sanity_paste.txt` | EC2 | 사용자별 `+kbd` 점수 분포 (개인화가 이상치를 만들지 않는지) |
| `ml_ec2_model_update_paste.txt` | ML EC2 | 모델 컨테이너·코드 위치·`/health` 확인 (재빌드 전) |
| `automation_check_paste.txt` ~ `check5` | EC2 | 2차 자동화 연결 점검: 기준선 테이블·cron 04:00·모델 로그·시간대 |
| `uninstall_check.py` · `uninstall_check_paste.txt` | EC2 (컨테이너 안) | 10/27 삭제 조치 뒤 잔존 접속 PC 점검. `SINCE`·`TEAM`(팀원 CP, 쉼표 구분) 환경변수 |
| `hourly_ids_paste.txt` | EC2 | 시간대별 접속 사용자 수 |
| `rds_tables_paste.txt` | EC2 | RDS 테이블 목록·행수 추정(`pg_stat_user_tables`) — DB 스키마 정리본 근거 |
| `verify_f04_error_json_paste.txt` | EC2 | 복약 LUI 오류 JSON(F04, 정화 PR #3) 배포 반영 여부 |
| `archive/2026-10_beta/` | — | 1차 베타(10/7~10/23) 수집 현황·QA 시트 8번·가설 S1~S4 검증. 다시 쓸 일 없음 |

정화 소유: `ddi_backend_qa.py`, `verify_ddi_sections.py`, `carepong_gold/export_rds.py` — 설명은 `docs/ddi/README.md`.

점검 **결과 파일**(사용자 CP·접속 시각 포함)은 저장소에 올리지 않는다 → 팀 드라이브 `final_docs/삭제조치_1027_점검결과/`.
