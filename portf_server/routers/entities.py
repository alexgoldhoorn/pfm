"""
Entities Router for Portfolio Management API

Handles broker and platform management.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_entities():
    """Get all entities."""
    return {"message": "Entities endpoint - under construction"}


@router.post("/")
async def create_entity():
    """Create a new entity."""
    return {"message": "Create entity endpoint - under construction"}
