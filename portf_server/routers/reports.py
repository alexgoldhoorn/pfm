"""
Reports Router — the general portfolio-report PDF.

Gathers data by calling other routers' endpoint functions directly, in
process (same "reuse, don't re-implement" pattern ``action_items.py`` uses
for its checks) rather than re-deriving net worth / performance / risk /
diversification, so this can never disagree with the pages it summarises.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..dependencies import get_database

router = APIRouter()
logger = logging.getLogger(__name__)

VALID_SECTIONS = ["networth", "performance", "diversification", "health"]


@router.get("/portfolio")
def get_portfolio_report_pdf(
    sections: Optional[str] = Query(
        None,
        description=(
            "Comma-separated subset of networth,performance,diversification,"
            "health. Default: all four."
        ),
    ),
    db=Depends(get_database),
):
    """General portfolio report as a PDF, with selectable sections.

    Each section reuses the exact function backing its own page/endpoint:
    ``networth.get_networth`` + ``portfolios.get_holdings`` (Net Worth page),
    ``analytics.get_performance`` + ``analytics.get_risk`` (Analytics page),
    ``analytics.get_diversification`` (fund look-through), and the cached
    Portfolio Health analysis (``portf:advisor:all`` in ``kv_cache`` — this
    does NOT trigger a fresh LLM run; generate it from the Research page
    first if you want it included).
    """
    from .analytics import get_diversification, get_performance, get_risk
    from .networth import get_networth
    from .portfolios import get_holdings
    from portf_manager.services.pdf_reports import build_portfolio_report_pdf

    if sections:
        requested = [s.strip() for s in sections.split(",") if s.strip()]
        invalid = [s for s in requested if s not in VALID_SECTIONS]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown section(s): {', '.join(invalid)}. "
                f"Valid: {', '.join(VALID_SECTIONS)}",
            )
    else:
        requested = list(VALID_SECTIONS)

    bundle: dict = {}
    if "networth" in requested:
        bundle["networth"] = get_networth(db=db, api_key_info={})
        bundle["holdings"] = get_holdings(portfolio_id=None, database=db)["holdings"]
    if "performance" in requested:
        bundle["performance"] = get_performance(
            benchmark="^GSPC", period="all", db=db, api_key_info={}
        )
        bundle["risk"] = get_risk(benchmark="^GSPC", db=db, api_key_info={})
    if "diversification" in requested:
        bundle["diversification"] = get_diversification(db=db, api_key_info={})
    if "health" in requested:
        try:
            bundle["health"] = db.cache_get("portf:advisor:all")
        except Exception as e:
            logger.warning(f"Portfolio report: reading cached health failed: {e}")
            bundle["health"] = None

    pdf_bytes = build_portfolio_report_pdf(requested, bundle)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=portfolio_report.pdf"},
    )
