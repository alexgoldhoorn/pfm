"""
Bookings Router for Portfolio Management API

Manages deposit and withdrawal bookings imported from PDT format.
"""

import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..dependencies import get_database

router = APIRouter()
logger = logging.getLogger(__name__)


class BookingCreate(BaseModel):
    portfolio_id: Optional[int] = None
    date: date
    action: str  # "Deposit" or "Withdrawal"
    amount: float
    currency: str = "EUR"


class BookingResponse(BaseModel):
    id: int
    portfolio_id: Optional[int] = None
    portfolio_name: Optional[str] = None
    date: date
    action: str
    amount: float
    currency: str


@router.post("/", response_model=BookingResponse, status_code=201)
def create_booking(body: BookingCreate, db=Depends(get_database)):
    """Create a manual deposit or withdrawal booking."""
    if body.action not in ("Deposit", "Withdrawal"):
        raise HTTPException(
            status_code=400, detail="action must be 'Deposit' or 'Withdrawal'"
        )
    try:
        booking_id = db.create_booking(
            date=body.date.isoformat(),
            action=body.action,
            amount=body.amount,
            currency=body.currency,
            portfolio_id=body.portfolio_id,
        )
        booking = db.get_booking(booking_id)
        return BookingResponse(**booking)
    except Exception:
        logger.exception("Error creating booking")
        raise HTTPException(status_code=500, detail="Error creating booking")


@router.get("/", response_model=List[BookingResponse])
def list_bookings(
    portfolio_id: Optional[int] = Query(
        default=None, description="Filter by portfolio ID"
    ),
    db=Depends(get_database),
):
    """Get all bookings (deposits and withdrawals)."""
    try:
        bookings = db.get_all_bookings(portfolio_id=portfolio_id)
        return [BookingResponse(**b) for b in bookings]
    except Exception:
        logger.exception("Error retrieving bookings")
        raise HTTPException(status_code=500, detail="Error retrieving bookings")


@router.delete("/{booking_id}", response_model=dict)
def delete_booking(booking_id: int, db=Depends(get_database)):
    """Delete a booking by ID."""
    try:
        booking = db.get_booking(booking_id)
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        db.delete_booking(booking_id)
        return {"message": "Booking deleted", "id": booking_id}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error deleting booking")
        raise HTTPException(status_code=500, detail="Error deleting booking")
