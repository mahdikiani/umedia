from utils.fuzzy import fuzzy_score


def test_ordered_subsequence_matches_case_insensitively() -> None:
    score = fuzzy_score("RPT", "annual-report.pdf")

    assert score is not None


def test_non_subsequence_is_excluded() -> None:
    assert fuzzy_score("rpt", "quarterly.txt") is None


def test_consecutive_matches_score_above_gapped_matches() -> None:
    consecutive = fuzzy_score("report", "report-final.pdf")
    gapped = fuzzy_score("report", "r-e-p-o-r-t-final.pdf")

    assert consecutive is not None
    assert gapped is not None
    assert consecutive > gapped


def test_start_and_separator_matches_receive_a_bonus() -> None:
    at_start = fuzzy_score("rep", "report.pdf")
    after_separator = fuzzy_score("rep", "annual_report.pdf")
    embedded = fuzzy_score("rep", "prereport.pdf")

    assert at_start is not None
    assert after_separator is not None
    assert embedded is not None
    assert at_start > embedded
    assert after_separator > embedded
