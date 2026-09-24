"""PDF report generation — the IRPF tax report and the general portfolio report.

Both builders take already-computed data dicts (the same ones the JSON
endpoints return) rather than querying the database themselves, so a PDF can
never disagree with the page/endpoint it's exported from. Renders with
reportlab (already a project dependency, pure-Python, no system libraries to
add to the Docker image).
"""

from datetime import datetime
from typing import Any, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import io

_STYLES = getSampleStyleSheet()

_TITLE_STYLE = ParagraphStyle(
    "PfmTitle", parent=_STYLES["Title"], fontSize=18, spaceAfter=4
)
_SUBTITLE_STYLE = ParagraphStyle(
    "PfmSubtitle",
    parent=_STYLES["Normal"],
    fontSize=10,
    textColor=colors.grey,
    spaceAfter=16,
)
_SECTION_STYLE = ParagraphStyle(
    "PfmSection",
    parent=_STYLES["Heading2"],
    fontSize=13,
    spaceBefore=18,
    spaceAfter=8,
    textColor=colors.HexColor("#1b1b1b"),
)
_NOTE_STYLE = ParagraphStyle(
    "PfmNote",
    parent=_STYLES["Normal"],
    fontSize=8,
    textColor=colors.grey,
    spaceBefore=6,
)
_BODY_STYLE = _STYLES["Normal"]

_HEADER_BG = colors.HexColor("#2c3e50")
_HEADER_FG = colors.whitesmoke
_ALT_BG = colors.HexColor("#f4f6f8")
_GAIN_COLOR = colors.HexColor("#1e7e34")
_LOSS_COLOR = colors.HexColor("#c0392b")


def _eur(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"€{value:,.2f}"


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}%"


def _styled_table(data: list[list[str]], col_widths=None, num_cols_right: int = 0):
    """A table with the app's standard header/zebra styling.

    ``num_cols_right`` right-aligns the last N columns (used for numeric
    columns) — everything else stays left-aligned.
    """
    table = Table(data, colWidths=col_widths, repeatRows=1)
    ncols = len(data[0]) if data else 0
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_FG),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ALT_BG]),
    ]
    if num_cols_right and ncols:
        style.append(("ALIGN", (ncols - num_cols_right, 0), (-1, -1), "RIGHT"))
    table.setStyle(TableStyle(style))
    return table


def _header(title: str, subtitle: str) -> list:
    return [
        Paragraph(title, _TITLE_STYLE),
        Paragraph(subtitle, _SUBTITLE_STYLE),
    ]


def _kv_table(rows: list[tuple[str, str]]) -> Table:
    data = [[k, v] for k, v in rows]
    table = Table(data, colWidths=[7 * cm, 6 * cm])
    table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#eeeeee")),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, _ALT_BG]),
            ]
        )
    )
    return table


# ── Tax report ───────────────────────────────────────────────────────────────


def build_tax_report_pdf(data: dict, estimated_tax: Optional[dict]) -> bytes:
    """Filing-ready Spanish IRPF report: realised gains (FIFO) + savings-base
    income (Box 27) + an estimated-tax summary.

    ``data`` is the return value of ``analytics.py``'s ``_build_tax_report_data``
    (same shape as ``GET /analytics/tax-report``); ``estimated_tax`` is the
    optional ``{"savings_base_eur", "estimated_tax_eur"}`` pair from
    ``current_year_savings_components`` / ``irpf_savings_tax``.

    Spain taxes realised capital gains, dividends and interest together in a
    single progressive "base del ahorro" — there is no long/short-term split
    like in the US, so the per-lot table has no such column.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        title=f"IRPF Tax Report {data['year']}",
    )

    story: list[Any] = []
    story += _header(
        f"IRPF Tax Report — {data['year']}",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · FIFO cost basis, "
        "amounts in EUR at transaction-date FX",
    )

    # Summary
    story.append(Paragraph("Summary", _SECTION_STYLE))
    summary_rows = [
        ("Realised capital gains/losses (all lots)", _eur(data["realised_gain_total"])),
        ("Lots sold this year", str(data["lot_count"])),
        (
            "Dividend income — Box 27 (gross)",
            _eur(data["dividends_gross_eur"]),
        ),
        ("Dividend withholding at source", _eur(data["dividend_withholding_eur"])),
        ("Interest withholding at source", _eur(data["interest_withholding_eur"])),
    ]
    if estimated_tax is not None:
        summary_rows.append(
            (
                "Savings base (ganancias + rendimientos)",
                _eur(estimated_tax["savings_base_eur"]),
            )
        )
        summary_rows.append(
            (
                "Estimated IRPF savings-base tax",
                _eur(estimated_tax["estimated_tax_eur"]),
            )
        )
    story.append(_kv_table(summary_rows))

    # Per-lot detail
    story.append(Paragraph("Realised Gains / Losses — Per Lot (FIFO)", _SECTION_STYLE))
    if data["realised_lots"]:
        rows = [
            [
                "Symbol",
                "Name",
                "Purchase",
                "Sale",
                "Qty",
                "Proceeds",
                "Cost basis",
                "Gain/Loss",
            ]
        ]
        for lot in data["realised_lots"]:
            rows.append(
                [
                    lot["symbol"],
                    (lot.get("name") or "")[:28],
                    lot["purchase_date"][:10],
                    lot["sell_date"][:10],
                    f"{lot['quantity']:.4f}",
                    _eur(lot["proceeds_eur"]),
                    _eur(lot["cost_basis_eur"]),
                    _eur(lot["gain_loss_eur"]),
                ]
            )
        table = _styled_table(
            rows,
            col_widths=[
                2 * cm,
                4 * cm,
                2.1 * cm,
                2.1 * cm,
                1.6 * cm,
                2.3 * cm,
                2.3 * cm,
                2.3 * cm,
            ],
            num_cols_right=4,
        )
        # Color-code the gain/loss column (last one) per data row.
        gl_col = len(rows[0]) - 1
        extra_style = []
        for i, lot in enumerate(data["realised_lots"], start=1):
            color = _GAIN_COLOR if lot["gain_loss_eur"] >= 0 else _LOSS_COLOR
            extra_style.append(("TEXTCOLOR", (gl_col, i), (gl_col, i), color))
        table.setStyle(TableStyle(extra_style))
        story.append(table)
    else:
        story.append(Paragraph("No realised sales in this period.", _BODY_STYLE))

    story.append(
        Paragraph(
            "Informational only — not tax advice. Verify figures with a gestor "
            "before filing. Realised gains use FIFO across full transaction "
            "history (prior-year sells still consume lots); proceeds convert at "
            "sell-date FX, cost basis at purchase-date FX.",
            _NOTE_STYLE,
        )
    )

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


# ── General portfolio report ────────────────────────────────────────────────

SECTION_TITLES = {
    "networth": "Net Worth & Holdings",
    "performance": "Performance & Risk",
    "diversification": "Diversification & Fund Look-Through",
    "health": "Portfolio Health",
}


def _networth_section(story: list, networth: dict, holdings: list) -> None:
    story.append(Paragraph(SECTION_TITLES["networth"], _SECTION_STYLE))
    rows = [
        ("Brokerage value", _eur(networth["brokerage_eur"])),
        ("Bank accounts", _eur(networth["bank_accounts_eur"])),
        ("Manual assets", _eur(networth["manual_assets_eur"])),
        ("Manual liabilities", _eur(-networth["manual_liabilities_eur"])),
        ("Active fixed deposits", _eur(networth["deposits_eur"])),
        ("Net worth", _eur(networth["net_worth_eur"])),
    ]
    story.append(_kv_table(rows))

    if holdings:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Current Holdings", _STYLES["Heading3"]))
        head = ["Symbol", "Name", "Type", "Qty", "Value (EUR)", "P&L %"]
        table_rows = [head]
        for h in sorted(
            holdings, key=lambda x: x.get("total_value_eur", 0), reverse=True
        ):
            table_rows.append(
                [
                    h.get("symbol", ""),
                    (h.get("name") or "")[:26],
                    h.get("asset_type", ""),
                    f"{h.get('quantity', 0):.4f}",
                    _eur(h.get("total_value_eur")),
                    _pct(h.get("pnl_pct")),
                ]
            )
        story.append(
            _styled_table(
                table_rows,
                col_widths=[2 * cm, 4.3 * cm, 2.2 * cm, 2 * cm, 2.8 * cm, 2.8 * cm],
                num_cols_right=2,
            )
        )
    else:
        story.append(Paragraph("No open holdings.", _BODY_STYLE))


def _performance_section(story: list, performance: dict, risk: dict) -> None:
    story.append(Paragraph(SECTION_TITLES["performance"], _SECTION_STYLE))
    perf_rows = [
        ("Invested", _eur(performance.get("invested_eur"))),
        ("Current value", _eur(performance.get("current_value_eur"))),
        ("Realised P&L", _eur(performance.get("realised_pnl_eur"))),
        ("Total return", _pct(performance.get("total_return_pct"))),
        ("Money-weighted IRR", _pct(performance.get("money_weighted_irr_pct"))),
        ("CAGR", _pct(performance.get("cagr_pct"))),
        (
            f"Benchmark ({performance.get('benchmark', '—')}) return",
            _pct(performance.get("benchmark_return_pct")),
        ),
    ]
    story.append(_kv_table(perf_rows))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Risk", _STYLES["Heading3"]))
    if risk.get("note"):
        story.append(Paragraph(risk["note"], _BODY_STYLE))
    else:
        risk_rows = [
            ("Max drawdown", _pct(risk.get("max_drawdown_pct"))),
            ("Volatility (annualised)", _pct(risk.get("volatility_pct"))),
            ("Sharpe ratio", str(risk.get("sharpe_ratio", "—"))),
            ("Sortino ratio", str(risk.get("sortino_ratio", "—"))),
            ("Calmar ratio", str(risk.get("calmar_ratio", "—"))),
            ("Beta", str(risk.get("beta", "—"))),
            ("Alpha", _pct(risk.get("alpha_pct"))),
        ]
        story.append(_kv_table(risk_rows))


def _breakdown_table(breakdown: dict) -> Optional[Table]:
    if not breakdown:
        return None
    rows = [["Category", "% of portfolio"]]
    for k, v in sorted(breakdown.items(), key=lambda kv: -kv[1]):
        rows.append([k, _pct(v)])
    return _styled_table(rows, col_widths=[9 * cm, 3 * cm], num_cols_right=1)


def _diversification_section(story: list, div: dict) -> None:
    story.append(Paragraph(SECTION_TITLES["diversification"], _SECTION_STYLE))
    coverage = div.get("coverage", {})
    story.append(
        Paragraph(
            f"{coverage.get('classified_pct', 0):.1f}% of portfolio value has a "
            f"region look-through profile; {len(coverage.get('unprofiled', []))} "
            "fund(s) still unprofiled.",
            _BODY_STYLE,
        )
    )
    story.append(Spacer(1, 8))

    for key, label in [
        ("by_asset_class", "By Asset Class"),
        ("by_region_equity", "By Region (look-through)"),
        ("by_currency_exposure", "By Currency Exposure (look-through)"),
        ("by_sector", "By Sector"),
    ]:
        table = _breakdown_table(div.get(key))
        if table is not None:
            story.append(Paragraph(label, _STYLES["Heading3"]))
            story.append(table)
            story.append(Spacer(1, 8))


def _health_section(story: list, health: Optional[dict]) -> None:
    story.append(Paragraph(SECTION_TITLES["health"], _SECTION_STYLE))
    if not health:
        story.append(
            Paragraph(
                "Portfolio Health has not been generated yet — open the Research "
                "page and run it once, then re-download this report to include it.",
                _BODY_STYLE,
            )
        )
        return

    if health.get("error"):
        story.append(Paragraph(health["error"], _BODY_STYLE))
        return

    scores = health.get("scores") or {}
    if scores:
        rows = [["Category", "Score (1–10)", "Reason"]]
        for name, cat in scores.items():
            rows.append(
                [
                    name.replace("_", " ").title(),
                    str(cat.get("score", "—")),
                    (cat.get("reason") or "")[:90],
                ]
            )
        story.append(
            _styled_table(
                rows, col_widths=[3.5 * cm, 2.2 * cm, 8.3 * cm], num_cols_right=1
            )
        )
        story.append(Spacer(1, 8))

    if health.get("summary"):
        story.append(Paragraph("Summary", _STYLES["Heading3"]))
        story.append(Paragraph(health["summary"], _BODY_STYLE))
        story.append(Spacer(1, 8))

    recs = health.get("recommendations") or []
    if recs:
        story.append(Paragraph("Recommendations", _STYLES["Heading3"]))
        for r in recs:
            if isinstance(r, dict):
                text = f"{r.get('category', '')}: {r.get('action', '')} — {r.get('rationale', '')}"
            else:
                text = str(r)
            story.append(Paragraph(f"• {text}", _BODY_STYLE))


_SECTION_BUILDERS = {
    "networth": _networth_section,
    "performance": _performance_section,
    "diversification": _diversification_section,
    "health": _health_section,
}


def build_portfolio_report_pdf(sections: list[str], bundle: dict) -> bytes:
    """General portfolio report PDF, with a subset of sections selected.

    ``sections`` is an ordered list of keys from ``SECTION_TITLES``.
    ``bundle`` carries whatever data each requested section needs, e.g.
    ``{"networth": {...}, "holdings": [...], "performance": {...},
    "risk": {...}, "diversification": {...}, "health": {...} | None}``.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        title="Portfolio Report",
    )

    story: list[Any] = []
    story += _header(
        "Portfolio Report",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    )

    for i, key in enumerate(sections):
        if i > 0:
            story.append(PageBreak())
        if key == "networth":
            _networth_section(story, bundle["networth"], bundle.get("holdings", []))
        elif key == "performance":
            _performance_section(story, bundle["performance"], bundle["risk"])
        elif key == "diversification":
            _diversification_section(story, bundle["diversification"])
        elif key == "health":
            _health_section(story, bundle.get("health"))

    story.append(
        Paragraph(
            "All figures in EUR. Informational only — not investment or tax " "advice.",
            _NOTE_STYLE,
        )
    )

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
