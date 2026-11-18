"""Action Items triage: families folded into groups, priority, top 5."""

from datetime import date
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from portf_manager.services import action_item_groups as groups
from portf_manager.services.action_item_acks import set_ack
from portf_manager.services.action_items import action_items_report

TODAY = date(2026, 11, 18)


def _target(n, triggers=("BUY",), since="2026-11-01", status="open"):
    return {
        "id": f"research:SYM{n}",
        "category": "watchlist",
        "severity": "medium",
        "title": f"Example {n} crossed a price target",
        "detail": "x",
        "link_page": "research",
        "context": {"symbol": f"SYM{n}", "triggers": list(triggers), "since": since},
        "status": status,
        "ack": None,
    }


def _pace(n, over, status="open", severity="low"):
    return {
        "id": f"budget:pace:{n}:2026-11",
        "category": "budget",
        "severity": severity,
        "title": f"On pace to exceed Line {n}",
        "detail": "x",
        "link_page": "budget",
        "context": {"label": f"Line {n}", "projected_over_eur": over},
        "status": status,
        "ack": None,
    }


def _prioritised(items):
    for i in items:
        i["priority"] = groups.priority(i, TODAY)
    return items


class TestFold:
    def test_two_stay_three_fold(self):
        two = _prioritised([_target(1), _target(2)])
        assert groups.fold(two, TODAY) == two
        three = _prioritised([_target(1), _target(2), _target(3)])
        (group,) = groups.fold(three, TODAY)
        assert group["id"] == "group:price_targets"
        assert group["kind"] == "group"
        assert group["family"] == "price_targets"
        assert group["count"] == 3
        assert group["title"] == "3 price targets crossed"
        assert group["link_page"] == "research"
        assert [c["id"] for c in group["children"]] == [
            "research:SYM1",
            "research:SYM2",
            "research:SYM3",
        ]

    def test_checked_and_snoozed_children_are_not_folded_or_counted(self):
        items = _prioritised(
            [
                _target(1),
                _target(2),
                _target(3, status="checked"),
                _target(4, status="snoozed"),
            ]
        )
        out = groups.fold(items, TODAY)
        assert out == items

        items = _prioritised(
            [_target(1), _target(2), _target(3), _target(4, status="checked")]
        )
        out = groups.fold(items, TODAY)
        assert [i["id"] for i in out] == ["group:price_targets", "research:SYM4"]
        assert out[0]["count"] == 3

    def test_group_takes_highest_child_severity(self):
        items = _prioritised(
            [
                _pace(1, 10),
                _pace(2, 20, severity="high"),
                _pace(3, 30, severity="medium"),
            ]
        )
        (group,) = groups.fold(items, TODAY)
        assert group["severity"] == "high"

    def test_price_target_summary(self):
        items = _prioritised(
            [
                _target(1, ("BUY",), since="2026-11-16"),
                _target(2, ("SELL",), since="2026-10-01"),
                _target(3, ("BUY", "SELL"), since="2026-11-18"),
            ]
        )
        (group,) = groups.fold(items, TODAY)
        assert group["detail"] == "2 BUY, 2 SELL; 2 new in the last 7 days."

    def test_pace_summary_names_three_largest_overshoots(self):
        items = _prioritised([_pace(1, 40), _pace(2, 400), _pace(3, 90), _pace(4, 250)])
        (group,) = groups.fold(items, TODAY)
        assert group["detail"] == (
            "Largest projected overshoots: Line 2 (+400 EUR), "
            "Line 4 (+250 EUR), Line 3 (+90 EUR)."
        )

    def test_item_outside_every_family_stays_alone(self):
        solo = _prioritised(
            [
                {
                    "id": "dq:duplicates",
                    "category": "data_quality",
                    "severity": "high",
                    "title": "t",
                    "context": {},
                    "status": "open",
                }
            ]
        )
        assert groups.fold(solo * 3, TODAY) == solo * 3

    def test_family_table_covers_the_real_id_prefixes(self):
        ids = {
            "research:A": "price_targets",
            "watchlist:A": "price_targets",
            "budget:pace:3:2026-11": "budget_pace",
            "budget:line:3": "budget_overrun",
            "recurring:missed:1:X:2026-11-01": "recurring_missed",
            "exposure:overlap:1,2": "fund_exposure",
            "exposure:profile:4": "fund_exposure",
            "import:portfolio:2": "stale_imports",
            "balance_sheet_stale:9": "cash_freshness",
            "recurring:price:1:X:2026-11-01": None,
            "budget:unbudgeted": None,
        }
        for item_id, family in ids.items():
            assert groups.family_of({"id": item_id}) == family


class TestPriority:
    def _item(self, **kw):
        base = {"id": "x", "category": "budget", "severity": "medium", "context": {}}
        base.update(kw)
        return base

    def test_severity_dominates(self):
        high_dq = self._item(severity="high", category="data_quality")
        mediums = [self._item(category="watchlist") for _ in range(20)]
        assert all(
            groups.priority(high_dq, TODAY) > groups.priority(m, TODAY) for m in mediums
        )

    def test_tax_with_few_days_left_beats_other_mediums(self):
        tax = self._item(category="tax", context={"days_left": 5})
        others = [
            self._item(category=c)
            for c in ("data_quality", "retirement", "allocate", "errors", "goals")
        ]
        assert all(
            groups.priority(tax, TODAY) > groups.priority(o, TODAY) for o in others
        )

    def test_urgency_scales_and_stops_past_the_window(self):
        far = groups.priority(self._item(context={"days_left": 60}), TODAY)
        mid = groups.priority(self._item(context={"days_left": 20}), TODAY)
        today = groups.priority(self._item(context={"days_left": 0}), TODAY)
        assert far == groups.priority(self._item(), TODAY)
        assert far < mid < today
        assert today - far == 50

    def test_due_date_counts_as_a_deadline(self):
        soon = self._item(context={"due_date": "2026-11-20"})
        assert groups.priority(soon, TODAY) > groups.priority(self._item(), TODAY)

    def test_sort_is_stable_for_ties(self):
        a = {"id": "a", "priority": 5}
        b = {"id": "b", "priority": 5}
        c = {"id": "c", "priority": 9}
        assert [i["id"] for i in groups.sort_by_priority([a, b, c])] == ["c", "a", "b"]


def _fake_items():
    items = [_target(n) for n in range(1, 8)]
    items += [_pace(n, 10 * n) for n in range(1, 4)]
    items.append(
        {
            "id": "dq:suspicious",
            "category": "data_quality",
            "severity": "high",
            "title": "Suspicious",
            "detail": "x",
            "link_page": "diagnostics",
            "context": {},
        }
    )
    items.append(
        {
            "id": "tax:year-end:2026",
            "category": "tax",
            "severity": "medium",
            "title": "Tax",
            "detail": "x",
            "link_page": "analytics",
            "context": {"days_left": 10},
        }
    )
    for i in items:
        i.pop("status", None)
        i.pop("ack", None)
    return items


@pytest.fixture
def fake_checks():
    with patch(
        "portf_manager.services.action_items.get_action_items",
        side_effect=lambda db: _fake_items(),
    ):
        yield


class TestReport:
    def test_ungrouped_is_unchanged_apart_from_priority(
        self, test_database, fake_checks
    ):
        report = action_items_report(test_database, today=TODAY)
        expected = _fake_items()
        assert [i["id"] for i in report["items"]] == [i["id"] for i in expected]
        for got, want in zip(report["items"], expected):
            assert got["priority"] == groups.priority(got, TODAY)
            assert got["family"] == groups.family_of(want)
            rest = {
                k: v
                for k, v in got.items()
                if k not in ("priority", "status", "ack", "family")
            }
            assert rest == want
        assert "top" not in report
        assert "open_entries" not in report["summary"]

    def test_every_item_carries_its_family_grouped_or_not(
        self, test_database, fake_checks
    ):
        for grouped in (False, True):
            report = action_items_report(test_database, today=TODAY, grouped=grouped)
            by_id = {i["id"]: i for i in report["items"]}
            assert by_id["dq:suspicious"]["family"] is None
            assert by_id["tax:year-end:2026"]["family"] is None
        plain = action_items_report(test_database, today=TODAY)
        assert {
            i["family"] for i in plain["items"] if i["id"].startswith("research:")
        } == {"price_targets"}

    def test_grouped_report_orders_by_priority_and_has_top_5(
        self, test_database, fake_checks
    ):
        report = action_items_report(test_database, today=TODAY, grouped=True)
        ids = [i["id"] for i in report["items"]]
        assert ids[0] == "dq:suspicious"
        assert ids[1] == "tax:year-end:2026"
        assert set(ids) == {
            "dq:suspicious",
            "tax:year-end:2026",
            "group:price_targets",
            "group:budget_pace",
        }
        assert report["summary"]["open"] == 12
        assert report["summary"]["open_entries"] == 4
        assert [i["id"] for i in report["top"]] == ids
        assert len(report["top"]) <= 5

    def test_top_is_capped_at_five(self, test_database):
        many = [
            {
                "id": f"dq:{n}",
                "category": "data_quality",
                "severity": "medium",
                "title": "t",
                "detail": "x",
                "link_page": "diagnostics",
                "context": {},
            }
            for n in range(9)
        ]
        with patch(
            "portf_manager.services.action_items.get_action_items",
            return_value=many,
        ):
            report = action_items_report(test_database, today=TODAY, grouped=True)
        assert len(report["top"]) == 5
        assert report["summary"]["open_entries"] == 9

    def test_checked_children_stay_out_of_the_group(self, test_database, fake_checks):
        set_ack(test_database, "research:SYM1", "checked")
        set_ack(test_database, "research:SYM2", "checked")
        set_ack(test_database, "research:SYM3", "checked")
        set_ack(test_database, "research:SYM4", "checked")
        # 3 open targets are left: still a group, of 3
        report = action_items_report(test_database, today=TODAY, grouped=True)
        group = next(i for i in report["items"] if i["id"] == "group:price_targets")
        assert group["count"] == 3
        set_ack(test_database, "research:SYM5", "snoozed", days=3)
        report = action_items_report(test_database, today=TODAY, grouped=True)
        assert not any(i["id"] == "group:price_targets" for i in report["items"])
        assert sum(1 for i in report["items"] if i["id"].startswith("research:")) == 2


class TestGroupAck:
    @pytest.mark.asyncio
    async def test_group_ack_touches_only_open_children(
        self, async_test_client: AsyncClient, auth_headers, test_database, fake_checks
    ):
        set_ack(test_database, "research:SYM1", "checked", note="mine")
        resp = await async_test_client.post(
            "/api/v1/action-items/ack",
            json={"id": "group:price_targets", "status": "snoozed", "days": 7},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "group:price_targets"
        assert body["children"] == [f"research:SYM{n}" for n in range(2, 8)]
        acks = (
            await async_test_client.get(
                "/api/v1/action-items/acks", headers=auth_headers
            )
        ).json()["acks"]
        assert "group:price_targets" not in acks
        assert acks["research:SYM1"]["status"] == "checked"
        assert acks["research:SYM1"]["note"] == "mine"
        assert acks["research:SYM4"]["status"] == "snoozed"
        assert "budget:pace:1:2026-11" not in acks

    @pytest.mark.asyncio
    async def test_unknown_family_is_422(
        self, async_test_client: AsyncClient, auth_headers, fake_checks
    ):
        resp = await async_test_client.post(
            "/api/v1/action-items/ack",
            json={"id": "group:nope", "status": "checked"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_grouped_endpoint(
        self, async_test_client: AsyncClient, auth_headers, fake_checks
    ):
        resp = await async_test_client.get(
            "/api/v1/action-items/?grouped=true", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["top"]) == 4
        assert data["summary"]["open_entries"] == 4
        plain = (
            await async_test_client.get("/api/v1/action-items/", headers=auth_headers)
        ).json()
        assert "top" not in plain
        assert all("priority" in i for i in plain["items"])
