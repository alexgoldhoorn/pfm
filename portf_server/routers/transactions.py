"""
Transactions Router for Portfolio Management API

Handles transaction recording and retrieval.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import List, Optional
from pydantic import BaseModel
from datetime import date

from ..dependencies import get_database
from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager

router = APIRouter()


class TransactionResponse(BaseModel):
    id: int
    asset_id: int
    portfolio_id: Optional[int] = None
    portfolio_name: Optional[str] = None
    transaction_type: str
    quantity: float
    price: float
    total_amount: float
    fees: float = 0.0
    transaction_date: date
    description: Optional[str] = None
    symbol: Optional[str] = None
    name: Optional[str] = None
    currency: Optional[str] = None


# API Key authentication dependency
async def get_api_key_auth_for_transactions(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    """Helper function for API key authentication in transaction endpoints."""
    return await require_api_key(api_key_manager)(request)


@router.get("/", response_model=List[TransactionResponse])
async def list_transactions(
    limit: Optional[int] = Query(
        default=100, description="Number of transactions to return"
    ),
    symbol: Optional[str] = Query(default=None, description="Filter by asset symbol"),
    portfolio_id: Optional[int] = Query(
        default=None, description="Filter by portfolio"
    ),
    db=Depends(get_database),
    api_key_info: dict = Depends(get_api_key_auth_for_transactions),
):
    """Get all transactions."""
    try:
        if symbol:
            asset_data = db.get_asset_by_symbol(symbol.upper())
            if not asset_data:
                raise HTTPException(
                    status_code=404,
                    detail=f"Asset with symbol '{symbol.upper()}' not found",
                )
            transactions = db.get_transactions_by_asset(asset_data["id"])
        else:
            transactions = db.get_all_transactions(
                limit=limit, portfolio_id=portfolio_id
            )

        return [TransactionResponse(**tx) for tx in transactions]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error retrieving transactions: {str(e)}"
        )


class TransactionCreateRequest(BaseModel):
    """Schema for creating a new transaction."""

    asset_id: int
    transaction_type: str
    quantity: float
    price: float
    total_amount: float
    transaction_date: str
    portfolio_id: Optional[int] = None
    fees: float = 0.0
    tax: float = 0.0
    currency: Optional[str] = None
    description: Optional[str] = None
    user_id: Optional[int] = None


@router.post("/", response_model=dict)
async def create_transaction(
    request: TransactionCreateRequest,
    db=Depends(get_database),
    api_key_info: dict = Depends(get_api_key_auth_for_transactions),
):
    """Create a new transaction."""
    try:
        # Validate that the asset exists
        asset = db.get_asset(request.asset_id)
        if not asset:
            raise HTTPException(
                status_code=404, detail=f"Asset with ID {request.asset_id} not found"
            )

        # Validate portfolio if provided
        if request.portfolio_id:
            portfolio = db.get_portfolio(request.portfolio_id)
            if not portfolio:
                raise HTTPException(
                    status_code=404,
                    detail=f"Portfolio with ID {request.portfolio_id} not found",
                )

        # Create the transaction
        transaction_id = db.create_transaction(
            asset_id=request.asset_id,
            transaction_type=request.transaction_type.lower(),
            quantity=request.quantity,
            price=request.price,
            total_amount=request.total_amount,
            transaction_date=request.transaction_date,
            portfolio_id=request.portfolio_id,
            fees=request.fees,
            tax=request.tax,
            currency=request.currency,
            description=request.description,
            user_id=request.user_id,
        )

        return {
            "message": "Transaction created successfully",
            "id": transaction_id,
            "asset_id": request.asset_id,
            "transaction_type": request.transaction_type,
            "quantity": request.quantity,
            "price": request.price,
            "transaction_date": request.transaction_date,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to create transaction: {str(e)}"
        )


@router.get("/{transaction_id}")
async def get_transaction(
    transaction_id: int,
    db=Depends(get_database),
    api_key_info: dict = Depends(get_api_key_auth_for_transactions),
):
    """Get a specific transaction by ID."""
    try:
        transaction = db.get_transaction(transaction_id)
        if not transaction:
            raise HTTPException(status_code=404, detail="Transaction not found")

        return TransactionResponse(**transaction)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error retrieving transaction: {str(e)}"
        )


class TransactionUpdateRequest(BaseModel):
    """Schema for updating a transaction."""

    quantity: Optional[float] = None
    price: Optional[float] = None
    fees: Optional[float] = None
    transaction_date: Optional[str] = None
    transaction_type: Optional[str] = None
    portfolio_id: Optional[int] = None
    description: Optional[str] = None


@router.put("/{transaction_id}", response_model=dict)
async def update_transaction(
    transaction_id: int,
    request: TransactionUpdateRequest,
    db=Depends(get_database),
    api_key_info: dict = Depends(get_api_key_auth_for_transactions),
):
    """Update a transaction."""
    try:
        # Check if transaction exists
        existing_transaction = db.get_transaction(transaction_id)
        if not existing_transaction:
            raise HTTPException(status_code=404, detail="Transaction not found")

        # Prepare update fields
        update_fields = {}
        if request.quantity is not None:
            update_fields["quantity"] = request.quantity
        if request.price is not None:
            update_fields["price"] = request.price
        if request.transaction_date is not None:
            update_fields["transaction_date"] = request.transaction_date
        if request.transaction_type is not None:
            update_fields["transaction_type"] = request.transaction_type.lower()
        if request.fees is not None:
            update_fields["fees"] = request.fees
        if request.portfolio_id is not None:
            update_fields["portfolio_id"] = request.portfolio_id
        if request.description is not None:
            update_fields["description"] = request.description

        # Recalculate total_amount when quantity, price, or fees change
        if (
            request.quantity is not None
            or request.price is not None
            or request.fees is not None
        ):
            new_quantity = (
                request.quantity
                if request.quantity is not None
                else existing_transaction["quantity"]
            )
            new_price = (
                request.price
                if request.price is not None
                else existing_transaction["price"]
            )
            new_fees = (
                request.fees
                if request.fees is not None
                else existing_transaction.get("fees", 0) or 0
            )
            tx_type = (
                request.transaction_type or existing_transaction["transaction_type"]
            ).lower()
            base = new_quantity * new_price
            update_fields["total_amount"] = (
                base - new_fees if tx_type == "sell" else base + new_fees
            )

        if not update_fields:
            raise HTTPException(status_code=400, detail="No fields provided for update")

        # Update the transaction
        success = db.update_transaction(transaction_id, **update_fields)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to update transaction")

        return {
            "message": "Transaction updated successfully",
            "id": transaction_id,
            "updated_fields": list(update_fields.keys()),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error updating transaction: {str(e)}"
        )


@router.delete("/{transaction_id}", response_model=dict)
async def delete_transaction(
    transaction_id: int,
    db=Depends(get_database),
    api_key_info: dict = Depends(get_api_key_auth_for_transactions),
):
    """Delete a transaction."""
    try:
        # Check if transaction exists
        existing_transaction = db.get_transaction(transaction_id)
        if not existing_transaction:
            raise HTTPException(status_code=404, detail="Transaction not found")

        # Delete the transaction
        success = db.delete_transaction(transaction_id)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete transaction")

        return {
            "message": "Transaction deleted successfully",
            "id": transaction_id,
            "deleted_transaction": existing_transaction,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error deleting transaction: {str(e)}"
        )
