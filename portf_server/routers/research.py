"""
Research & Valuation Router

GET    /api/v1/research/{symbol}            — get cached report (or 404)
POST   /api/v1/research/{symbol}/generate   — run LLM analysis, cache result
GET    /api/v1/research/{symbol}/targets    — get price targets
PUT    /api/v1/research/{symbol}/targets    — set price targets
GET    /api/v1/research/alerts/check        — check all targets vs latest prices
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from portf_manager import market
from portf_manager.positions import _sort_key, compute_positions

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)


class PriceTargetBody(BaseModel):
    buy_below: Optional[float] = None
    sell_above: Optional[float] = None
    fair_value: Optional[float] = None
    notes: Optional[str] = None


class ResearchSaveBody(BaseModel):
    thesis: Optional[str] = None
    conviction: Optional[int] = None
    method: Optional[str] = None
    assumptions: Optional[dict] = None
    fair_value: Optional[float] = None
    buy_below: Optional[float] = None
    sell_above: Optional[float] = None
    llm_summary: Optional[str] = None
    sources: Optional[list] = None
    current_price: Optional[float] = None
    # When False, the note is saved but existing alert targets are left untouched
    # (the web client sets this after asking the user whether to overwrite a
    # differing target). None/True keeps the legacy "always push targets" behaviour.
    update_targets: Optional[bool] = None


class AdvisorSettingsBody(BaseModel):
    cache_ttl_hours: int = 24


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _resolve_asset(db, symbol: str) -> dict:
    asset = db.get_asset_by_symbol(symbol.upper())
    if not asset:
        raise HTTPException(status_code=404, detail=f"Asset '{symbol}' not found")
    return asset


def _position_stats(db, asset: Optional[dict]) -> dict:
    """Current quantity, average cost, remaining cost basis, and realised P&L.

    Uses compute_positions (chronological, handles splits) so this stays
    consistent with the holdings and analytics endpoints.
    """
    if not asset:
        return {"quantity": 0.0, "avg_cost": 0.0, "cost_basis": 0.0, "realised": 0.0}
    txns = db.get_transactions_by_asset(asset["id"])
    positions, realised = compute_positions(txns)
    pos = positions.get(asset["id"], {"quantity": 0.0, "cost": 0.0})
    qty = pos["quantity"]
    cost = pos["cost"]
    return {
        "quantity": qty,
        "avg_cost": cost / qty if qty > 0 else 0.0,
        "cost_basis": cost,
        "realised": realised,
    }


def _cost_evolution(db, asset: Optional[dict]) -> tuple[list, list]:
    """Return (transactions, cost_evolution_series) for the research panel.

    transactions: most-recent-first list of {date, type, quantity, price,
    total, currency}. cost_evolution: chronological points of
    {date, quantity, avg_cost, invested} after each buy/sell/split.
    """
    if not asset:
        return [], []
    rows = db.get_transactions_by_asset(asset["id"])
    txns = [
        {
            "date": str(tx["transaction_date"])[:10],
            "type": tx["transaction_type"].lower(),
            "quantity": float(tx["quantity"]),
            "price": float(tx["price"] or 0),
            "total": float(tx["total_amount"] or 0),
            "currency": tx.get("currency", "EUR"),
        }
        for tx in rows
    ]
    # Replay transactions in chronological order to build the series.
    qty = cost = 0.0
    series = []
    for tx in sorted(rows, key=_sort_key):
        t = tx["transaction_type"].lower()
        q = float(tx["quantity"])
        total = float(tx["total_amount"] or 0)
        if t == "buy":
            qty += q
            cost += total
        elif t == "sell" and qty > 0:
            cost *= max(qty - q, 0.0) / qty
            qty -= q
        elif t == "split" and q > 0:
            qty *= q
        series.append(
            {
                "date": str(tx["transaction_date"])[:10],
                "quantity": round(qty, 6),
                "avg_cost": round(cost / qty, 4) if qty > 0 else 0.0,
                "invested": round(cost, 2),
                # the price/type of THIS transaction, so the chart can mark entries
                "tx_type": t,
                "tx_price": float(tx["price"] or 0),
            }
        )
    return txns, series


def _current_price(db, asset: Optional[dict], symbol: str) -> tuple[float, str]:
    """Latest price + currency: stored price for held assets, else the shared
    market-data cache (15-min freshness)."""
    if asset:
        pd_ = db.get_latest_price(asset["id"])
        if pd_:
            return float(pd_["price"]), asset.get("currency", "EUR")
    q = market.get_quote(db, symbol, max_age=900)
    if q.get("price"):
        return float(q["price"]), q.get("currency") or "EUR"
    return 0.0, (asset.get("currency", "EUR") if asset else "EUR")


# ── Portfolio Health Analysis ─────────────────────────────────────────────────


@router.get("/portfolio-analysis/settings")
async def get_advisor_settings(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    ttl = db.get_setting("advisor_cache_ttl_hours")
    return {"cache_ttl_hours": int(ttl) if ttl else 24}


@router.put("/portfolio-analysis/settings")
async def put_advisor_settings(
    body: AdvisorSettingsBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    ttl = max(1, min(168, body.cache_ttl_hours))
    db.set_setting("advisor_cache_ttl_hours", str(ttl))
    return {"cache_ttl_hours": ttl}


@router.get("/portfolio-analysis")
def get_portfolio_analysis(
    portfolio_id: Optional[int] = None,
    refresh: bool = False,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Run (or return cached) LLM portfolio health analysis."""
    from portf_manager.llm_client import get_llm_client
    from portf_manager.services.portfolio_advisor import (
        build_analysis_prompt,
        gather_diversification,
        gather_fees_and_dividends,
        gather_holdings_fundamentals,
        gather_performance,
        gather_risk,
        gather_tax,
        parse_analysis_response,
    )

    cache_key = f"portf:advisor:{portfolio_id or 'all'}"
    ttl_raw = db.get_setting("advisor_cache_ttl_hours")
    ttl_secs = int(ttl_raw) * 3600 if ttl_raw else 24 * 3600

    # Force-refresh: delete cached entry
    if refresh:
        try:
            db.cache_clear(prefix=cache_key)
        except Exception:
            pass
    else:
        # Return cached result if still valid
        cached = db.cache_get(cache_key)
        if cached is not None:
            return cached

    # Gather data in parallel (each thread returns (name, data_or_None))
    data_warnings: list[str] = []
    bundle: dict[str, Any] = {}

    def _run(name: str, fn, *args):
        try:
            return name, fn(*args)
        except Exception as e:
            logger.warning(f"Advisor '{name}' failed: {e}")
            return name, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            pool.submit(_run, "performance", gather_performance, db, portfolio_id),
            pool.submit(_run, "risk", gather_risk, db),
            pool.submit(
                _run, "diversification", gather_diversification, db, portfolio_id
            ),
            pool.submit(
                _run, "fees_and_dividends", gather_fees_and_dividends, db, portfolio_id
            ),
            pool.submit(_run, "tax", gather_tax, db, portfolio_id),
            pool.submit(
                _run, "holdings", gather_holdings_fundamentals, db, portfolio_id
            ),
        ]
        try:
            for fut in as_completed(futures, timeout=55):
                name, result = fut.result()
                if result is None:
                    data_warnings.append(f"{name} data unavailable")
                else:
                    bundle[name] = result
        except TimeoutError:
            data_warnings.append(
                "Some data gathering timed out; results may be partial"
            )
            for fut in futures:
                if fut.done() and not fut.cancelled():
                    try:
                        name, result = fut.result()
                        if result is not None and name not in bundle:
                            bundle[name] = result
                    except Exception:
                        pass

    if not bundle:
        return {
            "error": "No portfolio data available. Add some transactions first.",
            "data_warnings": data_warnings,
        }

    prompt = build_analysis_prompt(bundle)
    try:
        llm = get_llm_client()
        raw = llm.generate(prompt).strip()
    except Exception as e:
        logger.error(f"LLM call failed for portfolio analysis: {e}")
        return {
            "error": "Analysis unavailable — LLM error. Try again.",
            "data_warnings": data_warnings,
        }

    result = parse_analysis_response(raw)
    if "error" not in result:
        from datetime import datetime, timezone

        result["generated_at"] = datetime.now(timezone.utc).isoformat()
        result["cache_ttl_hours"] = ttl_secs // 3600
        result["data_warnings"] = data_warnings
        try:
            db.cache_set(cache_key, result, ttl_secs)
        except Exception as e:
            logger.warning(f"Failed to cache advisor result: {e}")

    return result


@router.get("/compare")
async def compare(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Latest saved research per symbol: price, fair value, upside, conviction."""
    out = []
    for n in db.get_latest_research_notes():
        sym = n["symbol"]
        asset = db.get_asset_by_symbol(sym)
        price, cur = _current_price(db, asset, sym)
        fair = n.get("fair_value")
        upside = round((fair - price) / price * 100, 1) if (fair and price) else None
        out.append(
            {
                "symbol": sym,
                "name": asset.get("name", "") if asset else "",
                "current_price": round(price, 4),
                "currency": cur,
                "fair_value": fair,
                "buy_below": n.get("buy_below"),
                "sell_above": n.get("sell_above"),
                "conviction": n.get("conviction"),
                "upside_pct": upside,
                "updated_at": n.get("created_at"),
            }
        )
    out.sort(key=lambda x: (x["upside_pct"] is None, -(x["upside_pct"] or 0)))
    return out


@router.get("/{symbol}")
async def get_report(
    symbol: str, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Return the cached research report for a symbol, or 404 if not yet generated."""
    asset = _resolve_asset(db, symbol)
    report = db.get_research_report(asset["id"])
    if not report:
        raise HTTPException(status_code=404, detail="No report yet — POST to /generate")
    if report.get("report_json"):
        report["details"] = json.loads(report["report_json"])
    return report


@router.post("/{symbol}/generate")
def generate_report(
    symbol: str, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Run web-augmented LLM valuation. Works for any symbol (held or not)."""
    from portf_manager.services.research import (
        fetch_fundamentals,
        fetch_recent_news,
        generate_valuation_report,
    )

    sym = symbol.upper()
    asset = db.get_asset_by_symbol(sym)  # may be None (research anything)
    pos = _position_stats(db, asset)
    current_price, currency = _current_price(db, asset, sym)
    fundamentals = fetch_fundamentals(sym, db)
    news = fetch_recent_news(sym, db=db)

    result = generate_valuation_report(
        symbol=sym,
        asset_name=asset.get("name", sym) if asset else sym,
        asset_type=asset.get("asset_type", "stock") if asset else "stock",
        current_price=current_price,
        avg_cost=pos["avg_cost"],
        currency=currency,
        fundamentals=fundamentals,
        news=news,
    )

    # Cache the LLM report only for assets we actually hold/track.
    if asset:
        db.upsert_research_report(
            asset_id=asset["id"],
            symbol=sym,
            fair_value=result.get("fair_value"),
            recommendation=result.get("recommendation", "HOLD"),
            confidence=result.get("confidence", "low"),
            summary=result.get("summary", ""),
            report_json=json.dumps(result),
        )

    result["symbol"] = sym
    result["current_price"] = current_price
    result["currency"] = currency
    result["avg_cost"] = round(pos["avg_cost"], 4)
    result["fundamentals"] = fundamentals
    return result


@router.get("/{symbol}/lookup")
def lookup(symbol: str, db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Snapshot for the research workbench (no LLM): price, position,
    fundamentals, news, existing targets and the latest saved research."""
    from portf_manager.services.research import fetch_fundamentals, fetch_recent_news

    sym = symbol.upper()
    asset = db.get_asset_by_symbol(sym)
    pos = _position_stats(db, asset)
    price, currency = _current_price(db, asset, sym)
    targets = db.get_price_target(asset["id"]) if asset else None
    notes = db.get_research_notes(sym)
    transactions, cost_evolution = _cost_evolution(db, asset)

    # Watchlist status (independent of whether we hold it)
    watch = next(
        (w for w in db.get_watchlist() if (w.get("symbol") or "").upper() == sym), None
    )

    # Position economics
    qty = pos["quantity"]
    cost_basis = pos["cost_basis"]
    market_value = qty * price
    unrealised = market_value - cost_basis if qty > 0 else 0.0
    unrealised_pct = (unrealised / cost_basis * 100) if cost_basis > 0 else None

    return {
        "symbol": sym,
        "name": asset.get("name") if asset else sym,
        "held": bool(asset and qty > 0),
        "on_watchlist": bool(watch),
        "watch_buy_below": watch.get("buy_below") if watch else None,
        "quantity": round(qty, 6),
        "avg_cost": round(pos["avg_cost"], 4),
        "cost_basis": round(cost_basis, 2),
        "market_value": round(market_value, 2),
        "unrealised_gain": round(unrealised, 2),
        "unrealised_pct": (
            round(unrealised_pct, 2) if unrealised_pct is not None else None
        ),
        "realised_gain": round(pos["realised"], 2),
        "current_price": round(price, 4),
        "currency": currency,
        "fundamentals": fetch_fundamentals(sym, db),
        "news": fetch_recent_news(sym, db=db),
        "targets": targets,
        "transactions": transactions,
        "cost_evolution": cost_evolution,
        "latest_note": notes[0] if notes else None,
    }


@router.post("/{symbol}/save")
async def save_research(
    symbol: str,
    body: ResearchSaveBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Save a versioned research record and (for held assets) push targets so
    price alerts fire."""
    sym = symbol.upper()
    asset = db.get_asset_by_symbol(sym)
    note_id = db.create_research_note(
        asset_id=asset["id"] if asset else None,
        symbol=sym,
        thesis=body.thesis,
        conviction=body.conviction,
        method=body.method,
        assumptions=json.dumps(body.assumptions) if body.assumptions else None,
        fair_value=body.fair_value,
        buy_below=body.buy_below,
        sell_above=body.sell_above,
        price_at_save=body.current_price,
        llm_summary=body.llm_summary,
        sources=json.dumps(body.sources) if body.sources else None,
    )
    # Saved research can drive price alerts for both held assets (price_targets,
    # buy + sell) and watchlisted-but-not-held symbols (watchlist.buy_below).
    # The client passes update_targets=False to save the note only (e.g. the user
    # declined to overwrite an existing, differing target).
    has_targets = bool(body.buy_below or body.sell_above or body.fair_value)
    do_update = has_targets and body.update_targets is not False
    targets_updated = False
    watchlist_updated = False
    if do_update:
        if asset:
            db.upsert_price_target(
                asset_id=asset["id"],
                buy_below=body.buy_below,
                sell_above=body.sell_above,
                fair_value=body.fair_value,
                notes=(body.thesis or "")[:500] or None,
            )
            targets_updated = True
        # If the symbol is on the watchlist, sync its buy zone too so the
        # watchlist alert fires off the researched buy price.
        on_watch = any(
            (w.get("symbol") or "").upper() == sym for w in db.get_watchlist()
        )
        if on_watch and body.buy_below:
            db.add_watchlist(symbol=sym, buy_below=body.buy_below)
            watchlist_updated = True
    return {
        "id": note_id,
        "symbol": sym,
        "targets_updated": targets_updated,
        "watchlist_updated": watchlist_updated,
        "held": bool(asset),
    }


@router.get("/{symbol}/history")
async def history(
    symbol: str, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Version history of saved research for a symbol."""
    notes = db.get_research_notes(symbol.upper())
    for n in notes:
        if n.get("assumptions"):
            try:
                n["assumptions"] = json.loads(n["assumptions"])
            except (TypeError, ValueError):
                pass
        if n.get("sources"):
            try:
                n["sources"] = json.loads(n["sources"])
            except (TypeError, ValueError):
                pass
    return notes


def _build_report(db, symbol: str) -> dict:
    """Assemble the full research dossier for a symbol from stored data.

    Pulls together everything we already keep: position economics (from your
    transactions), current fundamentals, price targets, the latest cached LLM
    valuation, and the full version history of saved research notes.
    """
    from portf_manager.services.research import fetch_fundamentals

    sym = symbol.upper()
    asset = db.get_asset_by_symbol(sym)
    pos = _position_stats(db, asset)
    price, currency = _current_price(db, asset, sym)
    transactions, _ = _cost_evolution(db, asset)
    qty = pos["quantity"]
    market_value = qty * price
    unrealised = market_value - pos["cost_basis"] if qty > 0 else 0.0

    notes = db.get_research_notes(sym)
    for n in notes:
        for k in ("assumptions", "sources"):
            if n.get(k):
                try:
                    n[k] = json.loads(n[k])
                except (TypeError, ValueError):
                    pass

    report = db.get_research_report(asset["id"]) if asset else None
    if report and report.get("report_json"):
        try:
            report["details"] = json.loads(report["report_json"])
        except (TypeError, ValueError):
            pass

    return {
        "symbol": sym,
        "name": asset.get("name") if asset else sym,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "currency": currency,
        "held": bool(asset and qty > 0),
        "position": {
            "quantity": round(qty, 6),
            "avg_cost": round(pos["avg_cost"], 4),
            "cost_basis": round(pos["cost_basis"], 2),
            "current_price": round(price, 4),
            "market_value": round(market_value, 2),
            "unrealised_gain": round(unrealised, 2),
            "realised_gain": round(pos["realised"], 2),
        },
        "fundamentals": fetch_fundamentals(sym, db),
        "targets": db.get_price_target(asset["id"]) if asset else None,
        "latest_llm_report": report,
        "notes": notes,
        "transactions": transactions,
    }


def _report_markdown(r: dict) -> str:
    """Render the assembled report dict as an archivable Markdown document."""
    cur = r.get("currency") or ""

    def m(v):
        return "—" if v is None else f"{v:,.2f} {cur}".strip()

    lines = [
        f"# Research report — {r['symbol']} ({r.get('name') or ''})",
        f"_Generated {r['generated_at']}_",
        "",
        "## Position (from your transactions, FIFO)",
    ]
    p = r["position"]
    if r["held"]:
        lines += [
            f"- Quantity held: **{p['quantity']}**",
            f"- Average cost: **{m(p['avg_cost'])}**",
            f"- Cost basis: **{m(p['cost_basis'])}**",
            f"- Current price: **{m(p['current_price'])}**",
            f"- Market value: **{m(p['market_value'])}**",
            f"- Unrealised P/L: **{m(p['unrealised_gain'])}**",
            f"- Realised P/L: **{m(p['realised_gain'])}**",
        ]
    else:
        lines.append("- Not currently held.")

    t = r.get("targets")
    if t:
        lines += [
            "",
            "## Price targets",
            f"- Buy below: {m(t.get('buy_below'))}",
            f"- Fair value: {m(t.get('fair_value'))}",
            f"- Sell above: {m(t.get('sell_above'))}",
        ]

    f = r.get("fundamentals") or {}
    keys = [k for k in f if k != "symbol"]
    if keys:
        lines += ["", "## Fundamentals (source: Yahoo Finance, TTM)"]
        lines += [f"- {k}: {f[k]}" for k in keys]

    llm = r.get("latest_llm_report")
    if llm:
        d = llm.get("details") or {}
        lines += [
            "",
            "## Latest LLM analysis (AI — not advice)",
            f"- Recommendation: **{llm.get('recommendation') or d.get('recommendation') or '—'}**",
            f"- Fair value: {m(llm.get('fair_value') or d.get('fair_value'))}",
            f"- Confidence: {llm.get('confidence') or d.get('confidence') or '—'}",
            "",
            (llm.get("summary") or d.get("summary") or "").strip(),
        ]
        for src in d.get("sources") or []:
            lines.append(
                f"  - source: {src.get('title') or src.get('url')} ({src.get('url')})"
            )

    notes = r.get("notes") or []
    if notes:
        lines += ["", f"## Saved research history ({len(notes)} entries)"]
        for n in notes:
            lines += [
                "",
                f"### {str(n.get('created_at'))[:19]} — conviction {n.get('conviction') or '—'}/5",
                f"- Method: {n.get('method') or '—'} · fair {m(n.get('fair_value'))}"
                f" · buy<{m(n.get('buy_below'))} · sell>{m(n.get('sell_above'))}",
            ]
            if n.get("assumptions"):
                lines.append(f"- Assumptions: {n['assumptions']}")
            if n.get("thesis"):
                lines += ["", n["thesis"].strip()]

    txns = r.get("transactions") or []
    if txns:
        lines += [
            "",
            "## Transactions",
            "",
            "| Date | Type | Qty | Price | Total |",
            "|---|---|--:|--:|--:|",
        ]
        for tx in txns:
            lines.append(
                f"| {tx['date']} | {tx['type']} | {tx['quantity']:g} | "
                f"{tx['price']:,.2f} | {tx['total']:,.2f} |"
            )

    lines += [
        "",
        "---",
        "_Portfolio Manager research report. Figures from your own transactions "
        "(FIFO) and Yahoo Finance; LLM commentary is AI-generated, not advice._",
    ]
    return "\n".join(lines)


@router.get("/{symbol}/report")
def research_report(
    symbol: str,
    format: str = "json",
    download: bool = False,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Full research dossier for a symbol (position, fundamentals, targets,
    LLM analysis, and saved-note history). ``format=md`` returns an archivable
    Markdown document; ``download=true`` sets a file-download header."""
    r = _build_report(db, symbol)
    if format.lower() in ("md", "markdown"):
        md = _report_markdown(r)
        headers = {}
        if download:
            fname = f"research_{r['symbol']}_{date.today().isoformat()}.md"
            headers["Content-Disposition"] = f'attachment; filename="{fname}"'
        return PlainTextResponse(md, media_type="text/markdown", headers=headers)
    return r


@router.get("/{symbol}/targets")
async def get_targets(
    symbol: str, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Return price targets for a symbol."""
    asset = _resolve_asset(db, symbol)
    targets = db.get_price_target(asset["id"])
    return targets or {}


@router.put("/{symbol}/targets")
async def set_targets(
    symbol: str,
    body: PriceTargetBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Create or update price targets for a symbol."""
    asset = _resolve_asset(db, symbol)
    db.upsert_price_target(
        asset_id=asset["id"],
        buy_below=body.buy_below,
        sell_above=body.sell_above,
        fair_value=body.fair_value,
        notes=body.notes,
    )
    return db.get_price_target(asset["id"])


@router.get("/alerts/check")
async def check_alerts(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """
    Compare all price targets against latest stored prices.
    Returns triggered alerts (does NOT send Telegram — use the cron for that).
    """
    # Current positions (quantity + cost basis) keyed by asset_id, so each alert
    # can report how much is held and the unrealised P&L if acted on.
    positions, _ = compute_positions(db.get_all_transactions())

    alerts = []
    for pt in db.get_all_price_targets():
        asset_id = pt["asset_id"]
        price_data = db.get_latest_price(asset_id)
        if not price_data:
            continue
        price = float(price_data["price"])
        symbol = pt["symbol"]
        triggered = []
        if pt.get("buy_below") and price <= pt["buy_below"]:
            triggered.append(
                {"type": "BUY", "threshold": pt["buy_below"], "price": price}
            )
        if pt.get("sell_above") and price >= pt["sell_above"]:
            triggered.append(
                {"type": "SELL", "threshold": pt["sell_above"], "price": price}
            )
        if triggered:
            asset = db.get_asset(asset_id) or {}
            pos = positions.get(asset_id, {"quantity": 0.0, "cost": 0.0})
            qty = round(pos["quantity"], 6) if pos["quantity"] > 0 else 0.0
            cost_basis = round(pos["cost"], 2) if qty else 0.0
            value = round(qty * price, 2)
            unrealized = round(value - cost_basis, 2) if qty else 0.0
            unrealized_pct = (
                round((value - cost_basis) / cost_basis * 100, 2)
                if cost_basis > 0
                else 0.0
            )
            alerts.append(
                {
                    "symbol": symbol,
                    "name": pt.get("name") or asset.get("name") or "",
                    "currency": asset.get("currency", "EUR"),
                    "price_date": price_data.get("price_date"),
                    "quantity": qty,
                    "avg_price": round(cost_basis / qty, 4) if qty else 0.0,
                    "cost_basis": cost_basis,
                    "value": value,
                    "unrealized_pnl": unrealized,
                    "unrealized_pnl_pct": unrealized_pct,
                    "triggers": triggered,
                }
            )
    # Dispatch push notifications for triggered alerts
    if alerts:
        try:
            from portf_manager.push_notifications import send_alerts_push

            send_alerts_push(db, alerts)
        except Exception as e:
            logger.warning(f"Push notification dispatch failed: {e}")
    return {"alerts": alerts, "total": len(alerts)}
