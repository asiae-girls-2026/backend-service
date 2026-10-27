from refresh_pipeline import parse_counts


def test_parse_med_counts():
    counts = parse_counts(
        "원천 총건수: 17,500\n이번 신규 INSERT: 20\n이번 변경 UPDATE: 5\n"
    )
    assert counts.fetched == 17500
    assert counts.inserted == 20
    assert counts.updated == 5
    assert counts.unchanged == 17475


def test_parse_hff_counts():
    counts = parse_counts("received: 1,200\ninserted: 100\n실제 UPDATE: 40\n")
    assert counts.fetched == 1200
    assert counts.inserted == 100
    assert counts.updated == 40
