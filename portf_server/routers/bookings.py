"""
Bookings Router for Portfolio Management API

Manages deposit and withdrawal bookings imported from PDT format.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from typing import List, Optional
from datetime import date

from ..dependencies import get_database
from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager

router = APIRouter()


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


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


@router.post("/", response_model=BookingResponse, status_code=201)
async def create_booking(
    body: BookingCreate,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating booking: {str(e)}")


@router.get("/", response_model=List[BookingResponse])
async def list_bookings(
    portfolio_id: Optional[int] = Query(
        default=None, description="Filter by portfolio ID"
    ),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Get all bookings (deposits and withdrawals)."""
    try:
        bookings = db.get_all_bookings(portfolio_id=portfolio_id)
        return [BookingResponse(**b) for b in bookings]
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error retrieving bookings: {str(e)}"
        )


@router.delete("/{booking_id}", response_model=dict)
async def delete_booking(
    booking_id: int,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Delete a booking by ID."""
    try:
        booking = db.get_booking(booking_id)
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        db.delete_booking(booking_id)
        return {"message": "Booking deleted", "id": booking_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting booking: {str(e)}")
