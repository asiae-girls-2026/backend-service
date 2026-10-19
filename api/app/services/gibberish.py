# -*- coding: utf-8 -*-
"""
무의미 입력(자판 난타) 판별 필터 — 케어퐁 공감챗 전처리용
================================================================
출처: 혜윤 gibberish_filter.py (2026-10-19, issues/20261019-01). 로직 그대로, routers/chat.py 가 Bedrock 호출 앞에서 is_meaningless() 를 부른다.
앱(ChatApp.tsx earnsHeart)도 같은 규칙으로 하트 판정 — 하트는 앱이 매기므로 서버 필터만으론 하트가 안 막힌다.
목적: "ㅁㅇㄴㄹ ㅈㄷ9ㅁ려" 같은 의미 없는 난타를 LLM 호출·하트 증가 전에 걸러냄.

쓰는 법 (서버/앱):
    from gibberish_filter import is_meaningless
    bad, reason = is_meaningless(user_text)
    if bad:
        # LLM 호출 X, 하트 증가 X
        return 고정_안내_멘트  # 예: "하고 싶은 말을 조금만 더 적어 주실래요?"
    else:
        # 평소대로 LLM(Bedrock) 호출

설계 원칙:
  - 멀쩡한 말은 절대 막지 않는다(= 오탐 최소화). 애매하면 통과시켜 LLM이 처리.
  - 인터넷 약어(ㅇㅇ, ㄱㅅ, ㅇㅋ...)·감정표현(ㅋㅋ, ㅠㅠ, ㅎㅎ)은 '의미 있음'으로 통과.
  - 외부 라이브러리 없이 동작(문자 종류 비율 기반). 더 정밀히 하려면 형태소 분석기 추가 가능.
"""

import re

# ── 튜닝 상수 (출력 보며 조정) ──────────────────────────────────
MIN_LEN = 2            # 공백 제거 후 이 미만이면 판단 보류(짧은 말은 그냥 통과)
JAMO_RATIO_CUTOFF = 0.5  # 낱자(ㄱ, ㅏ 등) 비율이 이 이상이면 난타 의심
JAMO_RUN_CUTOFF = 4      # 낱자만으로 이 글자 수 이상이면 난타로 봄

# 그대로 통과시킬 화이트리스트 (의미 있는 짧은 표현)
#  - 감정표현 낱자: ㅋ ㅎ ㅠ ㅜ ㅡ ㅗ (ㅋㅋㅋ, ㅠㅠ, ㅎㅎ, ㅜㅜ ...)
#  - 자주 쓰는 인터넷 약어 (전부 소문자/공백 제거 후 비교)
EMOTION_JAMO = set("ㅋㅎㅠㅜㅡㅗ")
ABBREV_WHITELIST = {
    "ㅇㅇ", "ㄴㄴ", "ㄱㅅ", "ㅇㅋ", "ㅊㅋ", "ㅂㅂ", "ㅅㄱ", "ㅎㅇ",
    "ㄷㄷ", "ㅈㅅ", "ㅇㄷ", "ㄱㄱ", "ㅇㅈ", "ㄴㅇㄱ",  # 필요 시 추가
}

# 유니코드 한글 범위
_SYLLABLE = re.compile(r"[가-힣]")       # 완성형 한글 (가~힣)
_JAMO = re.compile(r"[ㄱ-ㅣ]")           # 낱자 자음·모음 (ㄱ~ㅣ)
_LATIN_DIGIT = re.compile(r"[A-Za-z0-9]")


def is_meaningless(text: str):
    """무의미(난타) 입력이면 (True, 사유), 아니면 (False, 사유) 반환."""
    if text is None:
        return True, "empty"
    s = text.strip()
    if not s:
        return True, "empty"

    # 공백 제거본으로 화이트리스트/비율 판단
    compact = re.sub(r"\s+", "", s)

    # 1) 화이트리스트: 그대로 의미 있음 → 통과
    if compact in ABBREV_WHITELIST:
        return False, "whitelist_abbrev"
    #    감정표현 낱자만으로 이뤄진 경우(ㅋㅋㅋ, ㅠㅠ, ㅎㅎ 등) → 통과
    if compact and all(ch in EMOTION_JAMO for ch in compact):
        return False, "emotion_jamo"

    # 2) 너무 짧으면 판단 보류(응/네/ㅇㅇ 등 정상 짧은 말 보호) → 통과
    if len(compact) < MIN_LEN:
        return False, "too_short_pass"

    # 3) 문자 종류 집계
    n = len(compact)
    n_syllable = len(_SYLLABLE.findall(compact))     # 완성형 한글
    n_jamo = len(_JAMO.findall(compact))             # 낱자(난타 신호)
    n_latin = len(_LATIN_DIGIT.findall(compact))     # 영문/숫자

    # 4) 낱자만으로 이뤄진 긴 문자열(ㅁㅇㄴㄹ...) → 난타
    #    (단, 같은 글자 반복은 위 감정표현에서 이미 걸러짐)
    if n_jamo == n and n >= JAMO_RUN_CUTOFF:
        return True, f"jamo_only(len={n})"

    # 5) 낱자 비율이 높음(완성형·단어가 거의 없고 낱자가 섞여 흩뿌려짐) → 난타
    #    예: "ㅈㄷ9ㅁ려" → 낱자 3 / 전체 6 = 0.5
    jamo_ratio = n_jamo / n if n else 0.0
    if jamo_ratio >= JAMO_RATIO_CUTOFF and n >= JAMO_RUN_CUTOFF:
        return True, f"high_jamo_ratio({jamo_ratio:.2f})"

    # 6) 의미 글자(완성형+영문/숫자단어)가 하나도 없음 → 난타
    if n_syllable == 0 and n_latin == 0 and n_jamo > 0:
        return True, "no_meaningful_char"

    # 그 외 → 통과(정상으로 간주, LLM이 처리)
    return False, "pass"


if __name__ == "__main__":
    tests = [
        # (입력, 기대: 막아야 하면 True)
        ("ㅁㅇㄴㄹ", True),
        ("ㅈㄷ9ㅁ려", True),
        ("ㅁㅇㄴㄹ ㅈㄷ9ㅁ려", True),
        ("ㄷㅈㄱㅂㅅㄷ", True),
        ("ㅋㅋㅋㅋ", False),
        ("ㅠㅠ", False),
        ("ㅎㅎ", False),
        ("ㅇㅇ", False),
        ("ㄱㅅ", False),
        ("오늘 야근 많아서 지쳐", False),
        ("배고파", False),
        ("응", False),
        ("회의가 또 잡혔어 진짜 지친다", False),
    ]
    ok = 0
    for t, expected in tests:
        bad, reason = is_meaningless(t)
        mark = "OK" if bad == expected else "XX"
        if bad == expected:
            ok += 1
        print(f"[{mark}] {t!r:40} -> 막음={bad} ({reason})")
    print(f"\n{ok}/{len(tests)} 통과")
