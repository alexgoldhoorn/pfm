"""Tests for the pure spending-rule engine."""

from portf_manager.services.spending_rules import (
    pick_category,
    rule_matches,
    rule_sort_key,
)


def _rule(pattern="SHOP", category="Groceries", **kw):
    return {"id": kw.pop("id", 1), "pattern": pattern, "category": category, **kw}


def _always(_category, _amount):
    return True


def test_matches_description_substring_case_insensitive():
    assert rule_matches(_rule("shop"), "EXAMPLE SHOP", None, -5, 1)


def test_matches_merchant_when_description_has_noise():
    rule = _rule("example shop")
    assert rule_matches(rule, "EXAMPLE   SHOP \\X", "EXAMPLE SHOP", -5, 1)


def test_blank_pattern_never_matches():
    assert not rule_matches(_rule("  "), "ANYTHING", "ANYTHING", -5, 1)


def test_account_condition():
    rule = _rule(portfolio_id=7)
    assert rule_matches(rule, "SHOP", None, -5, 7)
    assert not rule_matches(rule, "SHOP", None, -5, 8)


def test_sign_condition():
    out_rule = _rule(amount_sign="negative")
    in_rule = _rule(amount_sign="positive")
    assert rule_matches(out_rule, "SHOP", None, -5, 1)
    assert not rule_matches(out_rule, "SHOP", None, 5, 1)
    assert rule_matches(in_rule, "SHOP", None, 5, 1)
    assert not rule_matches(in_rule, "SHOP", None, -5, 1)


def test_amount_range_is_inclusive_on_absolute_value():
    rule = _rule(min_amount=10.0, max_amount=20.0)
    assert rule_matches(rule, "SHOP", None, -10.0, 1)
    assert rule_matches(rule, "SHOP", None, -20.0, 1)
    assert not rule_matches(rule, "SHOP", None, -9.99, 1)
    assert not rule_matches(rule, "SHOP", None, 20.01, 1)


def test_sort_key_priority_then_id():
    rules = [_rule(id=1), _rule(id=2, priority=5), _rule(id=3, priority=None)]
    assert [r["id"] for r in sorted(rules, key=rule_sort_key)] == [2, 1, 3]


def test_pick_category_respects_priority():
    rules = [
        _rule("BIZUM", "Gifts", id=1),
        _rule("BIZUM", "Dinners", id=2, priority=1),
    ]
    assert pick_category(rules, "BIZUM X", None, -5, 1, _always) == "Dinners"


def test_root_mismatch_falls_through_to_next_rule():
    rules = [
        _rule("BIZUM", "Salary", id=1),
        _rule("BIZUM", "Gifts", id=2),
    ]

    def fits(category, amount):
        return not (category == "Salary" and amount < 0)

    assert pick_category(rules, "BIZUM X", None, -5, 1, fits) == "Gifts"


def test_no_match_is_uncategorized():
    assert pick_category([_rule("ZZZ")], "SHOP", None, -5, 1, _always) == (
        "uncategorized"
    )
