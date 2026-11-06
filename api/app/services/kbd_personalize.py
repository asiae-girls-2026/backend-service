# -*- coding: utf-8 -*-
# [벤더링] 원본: github.com/asiae-girls-2026/carepong-automation keyboard/kbd_personalize.py (def16a7, 혜윤).
#   api 컨테이너에서 routers/windows.py 가 conn=(api 의 psycopg3 연결) 을 넘겨 호출한다 — _db()/psycopg2 경로는 안 쓴다.
#   원본이 바뀌면 이 파일을 통째로 교체하고 위 커밋 해시만 갱신. 로직 수정 금지.
"""
kbd_personalize.py — 키보드(1차) 서빙용 개인화 글루 (실시간 적용)
=================================================================
[무엇] 키보드 모델이 점수 내기 직전에 호출. 그 유저의 최근 30분 행동을
       "개인 평균 중심 + 전체 공통 표준편차"(shrinkage)로 변환해 모델 입력 벡터를 만듦.
       기준선(평소값)은 kbd_baseline.py 배치가 하루 1번 갱신한 캐시를 읽음 → 적용은 실시간.

[레시피] z = (x − mu_eff) / global_std,   mu_eff = w·개인평균 + (1−w)·전체평균,  w = n/(n+k)
         · 중심은 개인, 스케일은 전체 공통(개인 std로 나누지 않음 — 역효과).
         · n(그 유저 평소 창 수) 커질수록 w→1 로 개인화 강해짐. 버전업 없이 자동.
         · 기준선 없는 신규 유저 → 전체 공통값으로(coldstart).

[키보드 서빙에 붙이기] 점수 계산 직전:
    from kbd_personalize import get_personalized_features
    x = get_personalized_features(user_id)          # 16-벡터 (FEATS 순서), 없으면 None
    if x is not None:
        score = model.predict_proba([x])[0, 1]
    # x 가 None(활동 없음/기준선 전혀 없음)이면 기존 로직대로 처리

[환경변수] DB_HOST/PORT/NAME/USER/PASSWORD (키보드 서버와 동일 RDS)
"""
import os, json, logging

log = logging.getLogger("kbd_personalize")

FEATS = ['SnKeyStrokes','SnChars','SnSpecialKeys','SnDirectionKeys','SnErrorKeys',
         'SnShortcutKeys','SnSpaces','CharactersRatio','SnMouseAct','SnLeftClicked',
         'SnRightClicked','SnDoubleClicked','SnWheel','SnDragged','SnMouseDistance','ErrorKeyRatio']
WINDOW_MIN = 30


def _db():
    host = os.environ.get("DB_HOST", "").strip()
    if not host:
        return None
    import psycopg2
    return psycopg2.connect(host=host, port=int(os.environ.get("DB_PORT","5432") or 5432),
        dbname=os.environ.get("DB_NAME","").strip(), user=os.environ.get("DB_USER","").strip(),
        password=os.environ.get("DB_PASSWORD","").strip(), connect_timeout=5)


def _expand(features):
    if isinstance(features, str):
        features = json.loads(features)
    return {f: (float(features[f]) if features.get(f) is not None else None) for f in FEATS}


def _aggregate_30min(cur, user_id):
    """그 유저 직전 30분 활동 창 평균(16피처 dict). 없으면 None."""
    cur.execute(
        "SELECT features FROM windows "
        "WHERE user_id=%s AND activity_zero=false "
        "AND window_start >= now() - interval '%s minutes' "
        "ORDER BY window_start DESC" % ("%s", WINDOW_MIN),
        (user_id,),
    )
    rows = cur.fetchall()
    if not rows:
        return None
    acc = {f: [] for f in FEATS}
    for (feats,) in rows:
        d = _expand(feats)
        for f in FEATS:
            if d[f] is not None:
                acc[f].append(d[f])
    out = {f: (sum(v)/len(v)) for f, v in acc.items() if v}
    return out if len(out) == len(FEATS) else None


def get_personalized_features(user_id, feats_30min=None, conn=None, k=None):
    """
    반환: shrinkage 개인화된 16-벡터(FEATS 순서, list[float]) 또는 None.
    - feats_30min 직접 넘기면 그걸 쓰고, 없으면 windows에서 직전 30분 집계.
    - conn 넘기면 재사용.
    """
    own = False
    if conn is None:
        conn = _db(); own = True
    if conn is None:
        log.warning("DB_HOST 미설정 — 개인화 건너뜀"); return None
    try:
        with conn.cursor() as cur:
            # 1) 이번 입력(직전 30분 평균)
            x = feats_30min or _aggregate_30min(cur, user_id)
            if not x:
                return None
            # 2) 전체 공통 seed
            cur.execute("SELECT mean_json, std_json, k FROM kbd_global_seed WHERE id=1")
            g = cur.fetchone()
            if not g:
                log.warning("kbd_global_seed 없음 — 배치(kbd_baseline.py) 먼저 실행 필요"); return None
            g_mean, g_std, g_k = g[0], g[1], g[2]
            g_mean = g_mean if isinstance(g_mean, dict) else json.loads(g_mean)
            g_std  = g_std  if isinstance(g_std, dict)  else json.loads(g_std)
            kk = k if k is not None else (g_k or 300)
            # 3) 개인 기준선
            cur.execute("SELECT n, mean_json, std_json FROM kbd_personal_baseline WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
            if row:
                n, p_mean, _ = row
                p_mean = p_mean if isinstance(p_mean, dict) else json.loads(p_mean)
            else:
                n, p_mean = 0, {}      # 신규 유저 → coldstart(전체 공통만)
    finally:
        if own:
            conn.close()

    w = n / (n + kk) if n else 0.0
    out = []
    for f in FEATS:
        xv = x[f]
        pm = p_mean.get(f, g_mean[f])
        mu_eff = w * pm + (1 - w) * g_mean[f]
        sd = g_std[f] if g_std[f] else 1.0     # 전체 공통 표준편차로 스케일
        out.append((xv - mu_eff) / sd)
    return out


if __name__ == "__main__":
    import sys
    uid = sys.argv[1] if len(sys.argv) > 1 else "CP-33948A80"
    v = get_personalized_features(uid)
    print(f"{uid}: {'개인화 벡터 ' + str([round(z,2) for z in v]) if v else '데이터 없음/coldstart'}")
