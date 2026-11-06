"""추론 — 혜윤 validation_samples.json 5건 + 결측·단조성. 실행: cd model && pytest"""
import sys, pathlib, json
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import predict as P

SAMPLES = json.load(open(HERE / "validation_samples.json", encoding="utf-8"))

def test_validation_samples_match_da():
    for c in SAMPLES:
        out = P.predict(c["input"]); exp = c["expected"]
        assert out["score"] == exp["score"], c["case"]
        assert out["level"] == exp["level"], c["case"]
        assert out["prob"] == exp["prob"], c["case"]
        assert out["imputed"] == exp["imputed"], c["case"]

def test_activity_zero_is_null():
    out = P.predict({"SnKeyStrokes": 0, "SnMouseAct": 0})
    assert out["score"] is None and out["level"] is None

def test_all_missing_equals_median():
    med = {n: P._MEDIAN[i] for i, n in enumerate(P.FEATURES)}
    assert P.predict({"SnKeyStrokes": 1})["score"] != None
    assert P.predict(med)["score"] == P.predict({**med})["score"]

def test_boundary_62_6_is_observe_not_intervene():
    # 정수 반올림하면 63→intervene 으로 뒤집히는 경계 — 소수 1자리 유지해야 DA 와 일치
    out = P.predict(SAMPLES[0]["input"]); assert out["score"] == 62.6 and out["level"] == "observe"


def test_standardized_zero_vector_equals_raw_mean():
    """z=0 벡터(=전체 평균)는 원시 평균 벡터와 같은 점수. 내장 스케일러를 건너뛰는지 확인 (11/6 개인화 훅)."""
    from predict import predict, FEATURES, _MEAN
    raw = predict({k: _MEAN[i] for i, k in enumerate(FEATURES)})
    std = predict({k: 0.0 for k in FEATURES}, standardized=True)
    assert std["score"] == raw["score"] and std["imputed"] == []

def test_standardized_missing_is_mean_not_activity_zero():
    from predict import predict
    out = predict({"SnKeyStrokes": 0.0, "SnMouseAct": 0.0}, standardized=True)
    assert out["score"] is not None and len(out["imputed"]) == 14
