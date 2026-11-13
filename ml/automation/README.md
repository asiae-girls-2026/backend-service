# ml/automation/

HRV·키보드 모델의 **개인 기준선(baseline) 계산·개인화 글루** 스크립트.
배치(주기 실행)로 유저별 "평소값"을 갱신하고, 서빙 시 실시간으로 개인화 변환에 사용.

- **출처 레포:** `asiae-girls-2026/carepong-automation`
- **커밋:** `1d61dfb`

## `hrv/` — PPG/HRV 개인 기준선
| 파일 | 내용 |
|------|------|
| `hrv_baseline.py` | RDS `ppg_measure`에서 유저의 안정(calm) HRV 측정을 모아 판정용 기준선 조회 |
| `build_hrv_baselines.py` | 유저별 안정 HRV 기준선을 배치 계산해 `ppg_personal_baseline`에 upsert (승격 현황 출력) |
| `stress_judge.py` | HRV 스트레스 판정 함수 |
| `run_hrv_baseline.sh` | 위 배치 실행 스크립트 |

## `keyboard/` — 키보드 개인 기준선
| 파일 | 내용 |
|------|------|
| `kbd_baseline.py` | RDS `windows`(16피처)를 훑어 유저별 평균·표준편차를 배치 계산 → `kbd_personal_baseline` + 글로벌 시드 저장 |
| `kbd_personalize.py` | 서빙용 실시간 개인화 글루 (shrinkage: 개인 중심 + 전체 공통 스케일로 입력 벡터 변환) |
| `run_kbd_baseline.sh` | 위 배치 실행 스크립트 |
