"""Category source precedence shared by previews and ledger writes."""

SOURCE_RANK = {"ml": 1, "plaid": 2, "rule": 3, "user": 4}


def _rank(source: str | None) -> int:
    return SOURCE_RANK.get(source or "", 0)


def can_replace_category(current_source: str | None, proposed_source: str) -> bool:
    """Equal or higher sources can replace a category. Missing and unknown sources rank lowest."""
    return _rank(proposed_source) >= _rank(current_source)
