# ml/ppg-hrv/

손가락 PPG 파형 기반 HRV 스트레스 모델과 파이프라인 코드.

- **출처 레포:** `asiae-girls-2026/carepong-ppg-hrv`
- **커밋:** `fd1cff8`

| 파일 | 내용 |
|------|------|
| `ppg_stress_base_maus_60s.pkl` | 학습된 서빙 모델 번들 (MAUS 60초 창, mean_hr·rmssd·sdnn·pnn50 기반 로지스틱 + 컷·집단기준) |
| `ppg_pipeline.py` | PPG 파형 → 전처리·SQI·피크검출·RR → HRV 피처 산출 파이프라인 |
| `maus_build_v5.py` | 위 `.pkl` 모델 번들을 만드는 학습/빌드 코드 (LOSO 평가 + 최종 학습) |
| `stress_judge.py` | 모델로 스트레스 점수·판정 산출 (콜드→개인 shrinkage 개인화) |
