# ml/

각 모델링 레포에서 가져온 모델 파일과 추론·파이프라인 코드를 모아둔 폴더입니다.
(기존 `model/` 서비스 폴더와는 별개 — 여기 파일은 원본 레포에서 복사해온 것입니다.)

| 폴더 | 출처 레포 | 커밋 | 내용 |
|------|-----------|------|------|
| `ppg-hrv/` | `asiae-girls-2026/carepong-ppg-hrv` | `fd1cff8` | PPG/HRV 스트레스 모델 (`ppg_stress_base_maus_60s.pkl`) + 파이프라인·빌드 코드 |
| `keyboard-mouse/` | `asiae-girls-2026/keyboard-mouse-1st-modeling` | `563d924` | 키보드/마우스 컨디션 모델 (`model_v1.json`, `stress_model.json`) + 추론 코드 `predict.py` |
| `automation/` | `asiae-girls-2026/carepong-automation` | `1d61dfb` | HRV·키보드 베이스라인/개인화 스크립트 |

> 참고: `keyboard-mouse` 모델(`model_v1.json`)은 서비스용으로 `../model/predict.py`에 이미 내장되어 있습니다. 여기 있는 파일은 원본 아티팩트/코드 보관용입니다.
