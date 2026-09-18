"""Tests for the rebalancing and research/valuation routers."""

import pytest
from httpx import AsyncClient
from fastapi import status

from portf_manager.services.rebalance_planner import STUB_PLAN_NOTICE, STUB_WARNING


class TestRebalance:
    @pytest.mark.asyncio
    async def test_set_and_get_targets(
        self, async_test_client: AsyncClient, auth_headers
    ):
        targets = [
            {"asset_type": "stock", "target_pct": 60.0},
            {"asset_type": "etf", "target_pct": 40.0},
        ]
        resp = await async_test_client.put(
            "/api/v1/rebalance/targets", json=targets, headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data) == 2

        resp2 = await async_test_client.get(
            "/api/v1/rebalance/targets", headers=auth_headers
        )
        assert resp2.status_code == status.HTTP_200_OK
        types = {t["asset_type"] for t in resp2.json()}
        assert types == {"stock", "etf"}

    @pytest.mark.asyncio
    async def test_analysis_shape(self, async_test_client: AsyncClient, auth_headers):
        resp = await async_test_client.get(
            "/api/v1/rebalance/analysis", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "total_value_eur" in data
        assert "allocations" in data
        assert "actions" in data
        assert isinstance(data["allocations"], list)

    @pytest.mark.asyncio
    async def test_target_pct_validation(
        self, async_test_client: AsyncClient, auth_headers
    ):
        # >100 should be rejected by pydantic
        resp = await async_test_client.put(
            "/api/v1/rebalance/targets",
            json=[{"asset_type": "stock", "target_pct": 150.0}],
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestRebalancePlan:
    """POST /api/v1/rebalance/plan — Task 2 skeleton.

    Every strategy's ``trades`` list is stubbed empty in this task (see
    ``portf_manager/services/rebalance_planner.py``); these tests cover the
    end-to-end response shape and request validation only, not real trade
    generation (Task 3's job).
    """

    async def _set_targets(self, client: AsyncClient, headers: dict, targets: list):
        resp = await client.put(
            "/api/v1/rebalance/targets", json=targets, headers=headers
        )
        assert resp.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_plan_shape(self, async_test_client: AsyncClient, auth_headers):
        # No target_overrides in the request => the planner must fall back
        # to saved allocation targets, so seed a valid (summing to 100) set
        # first.
        await self._set_targets(
            async_test_client,
            auth_headers,
            [
                {"asset_type": "stock", "target_pct": 60.0},
                {"asset_type": "etf", "target_pct": 40.0},
            ],
        )

        resp = await async_test_client.post(
            "/api/v1/rebalance/plan", json={}, headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()

        assert "generated_at" in data
        assert "inputs" in data
        assert data["inputs"]["strategy"] == "balanced"
        assert data["inputs"]["max_trades"] == 12

        assert "before" in data
        assert "total_value_eur" in data["before"]
        assert isinstance(data["before"]["allocations"], list)

        # The top-level list must carry the stub notice too — a client that
        # only checks `warnings` must not read a stubbed plan as clean.
        # Compared against the constant, not a literal, so Task 3's rewording
        # can't leave this assertion silently verifying nothing.
        assert isinstance(data["warnings"], list)
        assert data["warnings"][0] == STUB_PLAN_NOTICE

        plans = data["plans"]
        assert {p["strategy"] for p in plans} == {
            "tax_minimal",
            "closest_to_target",
            "balanced",
        }
        for plan in plans:
            assert plan["trades"] == []
            summary = plan["summary"]
            assert summary["trade_count"] == 0
            assert summary["buy_total_eur"] == 0.0
            assert summary["sell_total_eur"] == 0.0
            assert summary["estimated_realized_gain_eur"] == 0.0
            assert summary["estimated_tax_delta_eur"] == 0.0
            assert "max_abs_drift_pct_after" in summary
            assert plan["warnings"][0] == STUB_WARNING

    @pytest.mark.asyncio
    async def test_plan_validation(self, async_test_client: AsyncClient, auth_headers):
        # Invalid strategy value.
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan",
            json={"strategy": "not_a_real_strategy"},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # max_trades out of range.
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan", json={"max_trades": 0}, headers=auth_headers
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        resp = await async_test_client.post(
            "/api/v1/rebalance/plan", json={"max_trades": 101}, headers=auth_headers
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # min_trade_eur negative.
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan",
            json={"min_trade_eur": -1},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # target_overrides summing outside 99.5..100.5 — a service/route-level
        # check (it merges with DB-stored targets), still a 422 like the
        # Pydantic-enforced cases above, for a consistent client contract.
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan",
            json={
                "target_overrides": [
                    {"asset_type": "stock", "target_pct": 60.0},
                    {"asset_type": "etf", "target_pct": 60.0},
                ]
            },
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "detail" in resp.json()

    @pytest.mark.asyncio
    async def test_plan_buy_only_mode(
        self, async_test_client: AsyncClient, auth_headers
    ):
        # allow_sells=false must be accepted — real buy-only behavior is
        # Task 3's job, this only confirms the field doesn't 422/500 and the
        # stub shape still comes back.
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan",
            json={
                "allow_sells": False,
                "target_overrides": [
                    {"asset_type": "stock", "target_pct": 55.0},
                    {"asset_type": "etf", "target_pct": 45.0},
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["inputs"]["allow_sells"] is False
        assert len(data["plans"]) == 3
        for plan in data["plans"]:
            assert plan["trades"] == []


class TestResearch:
    @pytest.mark.asyncio
    async def test_report_404_when_none(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        # Create an asset first
        await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        resp = await async_test_client.get(
            f"/api/v1/research/{sample_asset_data['symbol']}", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_unknown_symbol_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/research/NOPE", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_set_and_get_price_targets(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        sym = sample_asset_data["symbol"]
        resp = await async_test_client.put(
            f"/api/v1/research/{sym}/targets",
            json={"buy_below": 100.0, "sell_above": 130.0},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["buy_below"] == 100.0
        assert data["sell_above"] == 130.0

    @pytest.mark.asyncio
    async def test_alerts_check_shape(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/research/alerts/check", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "alerts" in data
        assert "total" in data


class TestUpsertPriceTargetClearSemantics:
    """`upsert_price_target`'s COALESCE-vs-`clear` split (database.py).

    Manual/partial saves keep passing ``None`` for an untouched field and
    must keep the other stored fields exactly as they were (COALESCE
    no-op). ``clear`` is the opposite signal — "this field was evaluated and
    is explicitly empty" — and must write a real NULL. Only the
    automated/bulk research writer (`research.py::_run_bulk_research_refresh`)
    passes `clear`; see `tests/unit/test_research_bulk_refresh.py` for that
    caller's own regression test.
    """

    def _asset(self, db, symbol="ZANDER"):
        # Invented ticker/name — public repo, no real holdings in fixtures.
        return db.create_asset(symbol, "Zander Testing Corp", "stock", currency="USD")

    def test_partial_manual_upsert_leaves_other_fields_intact(self, test_database):
        aid = self._asset(test_database)
        test_database.upsert_price_target(
            asset_id=aid,
            buy_below=10.0,
            sell_above=20.0,
            fair_value=15.0,
            notes="Initial manual note.",
        )

        # The manual UI's "edit one field" case: only buy_below is passed,
        # everything else arrives as None.
        test_database.upsert_price_target(asset_id=aid, buy_below=11.0)

        target = test_database.get_price_target(aid)
        assert target["buy_below"] == 11.0
        assert target["sell_above"] == 20.0
        assert target["fair_value"] == 15.0
        assert target["notes"] == "Initial manual note."

    def test_clear_nulls_only_the_named_field(self, test_database):
        aid = self._asset(test_database)
        test_database.upsert_price_target(
            asset_id=aid, buy_below=10.0, sell_above=20.0, fair_value=15.0
        )

        test_database.upsert_price_target(asset_id=aid, clear={"fair_value"})

        target = test_database.get_price_target(aid)
        assert target["fair_value"] is None
        assert target["buy_below"] == 10.0
        assert target["sell_above"] == 20.0

    def test_clear_overrides_a_value_passed_for_the_same_field(self, test_database):
        aid = self._asset(test_database)
        test_database.upsert_price_target(asset_id=aid, fair_value=15.0)

        # A value passed alongside clear for the same column is ignored.
        test_database.upsert_price_target(
            asset_id=aid, fair_value=99.0, clear={"fair_value"}
        )

        assert test_database.get_price_target(aid)["fair_value"] is None

    def test_clear_still_moves_updated_at(self, test_database):
        aid = self._asset(test_database)
        test_database.upsert_price_target(asset_id=aid, fair_value=15.0)
        with test_database.get_connection() as conn:
            conn.execute(
                "UPDATE price_targets SET updated_at = '2020-01-01 00:00:00' "
                "WHERE asset_id = ?",
                (aid,),
            )
            conn.commit()

        test_database.upsert_price_target(asset_id=aid, clear={"fair_value"})

        assert (
            test_database.get_price_target(aid)["updated_at"] != "2020-01-01 00:00:00"
        )

    def test_unknown_clear_field_raises(self, test_database):
        aid = self._asset(test_database)
        with pytest.raises(ValueError):
            test_database.upsert_price_target(asset_id=aid, clear={"bogus_field"})
