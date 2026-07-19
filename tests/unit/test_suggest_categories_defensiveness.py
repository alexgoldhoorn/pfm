"""Tests for defensive parsing in spending.suggest_categories.

Covers a reviewer-flagged bug: syntactically valid but mis-shaped LLM JSON
(e.g. a flat list of strings/ints/nulls instead of the requested list of
objects) used to raise an uncaught AttributeError inside the per-item
parsing loop, producing a raw 500 instead of the intended 502-with-message
behavior already used elsewhere in this endpoint for LLM-output problems.
"""

from portf_server.routers.spending import CategorySuggestion, _parse_suggestions


def test_parse_suggestions_skips_non_dict_items():
    """Non-dict elements (str/int/None) must be skipped, not raise."""
    data = ["Groceries", "Dining", 1, 2, 3, None]

    result = _parse_suggestions(data)

    assert result == []


def test_parse_suggestions_skips_non_dict_items_mixed_with_valid():
    """A malformed item alongside valid ones should not crash the batch."""
    data = [
        {"description": "SUPERMARKET X", "category": "Groceries"},
        "not a dict",
        None,
        {"description": "RESTAURANT Y", "category": "Dining"},
    ]

    result = _parse_suggestions(data)

    assert [s.description for s in result] == ["SUPERMARKET X", "RESTAURANT Y"]
    assert all(isinstance(s, CategorySuggestion) for s in result)


def test_parse_suggestions_treats_explicit_null_description_as_missing():
    """`"description": null` must fall back to "" (not the string "None")."""
    data = [{"description": None, "category": "Groceries"}]

    result = _parse_suggestions(data)

    assert result == []


def test_parse_suggestions_treats_explicit_null_category_and_pattern_as_default():
    """`category`/`suggested_pattern` nulls should fall back, not become "None"."""
    data = [
        {
            "description": "SUPERMARKET X",
            "category": None,
            "suggested_pattern": None,
        }
    ]

    result = _parse_suggestions(data)

    assert len(result) == 1
    assert result[0].category == "Other"
    assert result[0].suggested_pattern == "SUPERMARKET X"[:20]


def test_parse_suggestions_happy_path_unchanged():
    """Valid well-formed input still produces the expected suggestions."""
    data = [
        {
            "description": "SUPERMARKET X",
            "category": "Groceries",
            "suggested_pattern": "SUPERMARKET X",
        },
        {
            "description": "RESTAURANT Y",
            "category": "Dining",
            "suggested_pattern": "RESTAURANT Y",
        },
    ]

    result = _parse_suggestions(data)

    assert len(result) == 2
    assert result[0] == CategorySuggestion(
        description="SUPERMARKET X",
        category="Groceries",
        suggested_pattern="SUPERMARKET X",
    )
    assert result[1] == CategorySuggestion(
        description="RESTAURANT Y",
        category="Dining",
        suggested_pattern="RESTAURANT Y",
    )


def test_parse_suggestions_non_list_top_level_returns_empty():
    """A top-level dict/str/int instead of a list yields no suggestions."""
    assert _parse_suggestions({"description": "x"}) == []
    assert _parse_suggestions("not a list") == []
    assert _parse_suggestions(None) == []
