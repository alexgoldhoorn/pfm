"""Saved reports: Markdown documents kept from the AI chat.

A report is a title plus Markdown (which may hold ``chart`` blocks the web
client draws). They are saved from a single chat answer, or written by the LLM
from a whole thread (``POST /api/v1/llm/chat/sessions/{id}/report``).
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from portf_manager.database import Database

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()

TITLE_MAX = 120
CONTENT_MAX = 400_000


class ReportCreate(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    content_md: str = Field(min_length=1, max_length=CONTENT_MAX)
    source_session_id: Optional[str] = Field(default=None, max_length=64)


class ReportUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=TITLE_MAX)
    content_md: Optional[str] = Field(
        default=None, min_length=1, max_length=CONTENT_MAX
    )


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _clean_title(title: str) -> str:
    cleaned = " ".join(title.split())
    if not cleaned:
        raise HTTPException(status_code=422, detail="title cannot be blank")
    return cleaned


@router.get("/")
def list_reports(
    db: Database = Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Saved reports without their content, newest first."""
    return {"items": db.list_reports()}


@router.post("/", status_code=201)
def create_report(
    body: ReportCreate,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Save a report."""
    report_id = db.create_report(
        _clean_title(body.title), body.content_md, body.source_session_id
    )
    return db.get_report(report_id)


@router.get("/{report_id}")
def get_report(
    report_id: int,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """One report with its content."""
    report = db.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.put("/{report_id}")
def update_report(
    report_id: int,
    body: ReportUpdate,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Rename a report or replace its content; a missing field is left as is."""
    title = _clean_title(body.title) if body.title is not None else None
    if not db.update_report(report_id, title=title, content_md=body.content_md):
        raise HTTPException(status_code=404, detail="Report not found")
    return db.get_report(report_id)


@router.delete("/{report_id}", status_code=204)
def delete_report(
    report_id: int,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Delete a report."""
    if not db.delete_report(report_id):
        raise HTTPException(status_code=404, detail="Report not found")
    return Response(status_code=204)
