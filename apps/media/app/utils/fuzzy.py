from typing import Final

_SEPARATORS: Final = frozenset("/ _-.")
_BASE_MATCH: Final = 10
_START_BONUS: Final = 10
_SEPARATOR_BONUS: Final = 8
_CONSECUTIVE_BONUS: Final = 12


def fuzzy_score(query: str, text: str) -> int | None:
    folded_query = query.casefold()
    folded_text = text.casefold()
    if not folded_query:
        return 0
    if not folded_text:
        return None

    previous: list[int | None] = [None] * len(folded_text)
    for index, character in enumerate(folded_text):
        if character != folded_query[0]:
            continue
        boundary_bonus = _START_BONUS if index == 0 else (
            _SEPARATOR_BONUS if folded_text[index - 1] in _SEPARATORS else 0
        )
        previous[index] = _BASE_MATCH + boundary_bonus - index

    for query_character in folded_query[1:]:
        current: list[int | None] = [None] * len(folded_text)
        best_gapped_prefix: int | None = None
        for index, character in enumerate(folded_text):
            if index >= 2 and previous[index - 2] is not None:
                candidate = previous[index - 2] + index - 2
                best_gapped_prefix = (
                    candidate
                    if best_gapped_prefix is None
                    else max(best_gapped_prefix, candidate)
                )
            if character != query_character:
                continue

            transitions: list[int] = []
            if index > 0 and previous[index - 1] is not None:
                transitions.append(previous[index - 1] + _CONSECUTIVE_BONUS)
            if best_gapped_prefix is not None:
                transitions.append(best_gapped_prefix - index + 1)
            if not transitions:
                continue

            boundary_bonus = (
                _SEPARATOR_BONUS if folded_text[index - 1] in _SEPARATORS else 0
            )
            current[index] = max(transitions) + _BASE_MATCH + boundary_bonus
        previous = current

    scores = [score for score in previous if score is not None]
    return max(scores) if scores else None
