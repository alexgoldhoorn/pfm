"""
Tax Router for Portfolio Management API

Handles tax calculation and reporting.
"""

import io
from datetime import date
from typing import Optional, List
from fastapi import APIRouter, HTTPException, status, Depends, Query, Response
from pydantic import BaseModel, Field

from portf_manager.tax_calculator import TaxCalculator
from portf_manager.tax_export import TaxReportExporter, generate_tax_report_filename
from ..dependencies import get_current_user_id, get_database

router = APIRouter()


class TaxReportRequest(BaseModel):
    """Schema for tax report request."""

    start_date: date = Field(..., description="Start date for tax report (YYYY-MM-DD)")
    end_date: date = Field(..., description="End date for tax report (YYYY-MM-DD)")
    symbols: Optional[List[str]] = Field(
        None, description="Optional list of symbols to filter by"
    )
    format: str = Field("csv", description="Output format: 'csv' or 'pdf'")


class TaxReportResponse(BaseModel):
    """Schema for tax report response."""

    message: str = Field(..., description="Success message")
    file_size: int = Field(..., description="Size of generated file in bytes")
    format: str = Field(..., description="Format of generated file")
    transaction_count: int = Field(
        ..., description="Number of tax transactions in report"
    )
    symbols_processed: List[str] = Field(
        ..., description="Symbols included in the report"
    )
    summary: dict = Field(..., description="Summary statistics")


@router.get("/report")
async def generate_tax_report(
    start_date: date = Query(..., description="Start date for tax report (YYYY-MM-DD)"),
    end_date: date = Query(..., description="End date for tax report (YYYY-MM-DD)"),
    symbols: Optional[str] = Query(
        None, description="Comma-separated list of symbols to filter by"
    ),
    format: str = Query("csv", description="Output format: 'csv' or 'pdf'"),
    current_user_id: int = Depends(get_current_user_id),
    database=Depends(get_database),
):
    """
    Generate tax report in CSV or PDF format based on user portfolio and date range.

    This endpoint calculates capital gains/losses using FIFO methodology and generates
    a report for tax filing purposes.

    Args:
        start_date: Start date for sell transactions to include in report
        end_date: End date for sell transactions to include in report
        symbols: Optional comma-separated list of symbols to filter by
        format: Output format ('csv' or 'pdf')
        current_user_id: Current user ID
        database: Database instance

    Returns:
        CSV or PDF file with tax report data

    Raises:
        HTTPException: If report generation fails
    """
    try:
        # Validate date range
        if start_date > end_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Start date must be before or equal to end date",
            )

        # Parse symbols list if provided
        symbol_list = None
        if symbols:
            symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

        # Validate format
        if format not in ["csv", "pdf"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Format must be 'csv' or 'pdf'",
            )

        # Initialize tax calculator
        tax_calculator = TaxCalculator(database)

        # Calculate tax report
        tax_report = tax_calculator.calculate_tax_report(
            user_id=current_user_id,
            start_date=start_date,
            end_date=end_date,
            symbols=symbol_list,
        )

        # Check if any transactions found
        if not tax_report:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No tax transactions found for the specified criteria",
            )

        # Generate summary
        summary = tax_calculator.generate_tax_summary(tax_report)

        # Count total transactions
        total_transactions = sum(
            len(transactions) for transactions in tax_report.values()
        )
        symbols_processed = list(tax_report.keys())

        if format == "csv":
            # Generate CSV report
            exporter = TaxReportExporter()

            # Create in-memory file
            csv_buffer = io.StringIO()

            # Write CSV header
            import csv

            writer = csv.writer(csv_buffer)
            exporter._write_header(writer)

            # Write transactions
            for symbol in sorted(tax_report.keys()):
                transactions = tax_report[symbol]
                for tx in sorted(transactions, key=lambda x: x.sell_date):
                    exporter._write_transaction_row(writer, tx)

            # Write summary section
            exporter._write_summary_section(writer, tax_report)

            # Get CSV content
            csv_content = csv_buffer.getvalue()
            csv_buffer.close()

            # Generate filename
            filename = generate_tax_report_filename(start_date, end_date, symbol_list)

            # Return CSV file
            return Response(
                content=csv_content,
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename={filename}",
                    "Content-Type": "text/csv",
                },
            )

        elif format == "pdf":
            # Generate PDF report
            try:
                from reportlab.lib.pagesizes import A4
                from reportlab.platypus import (
                    SimpleDocTemplate,
                    Table,
                    TableStyle,
                    Paragraph,
                    Spacer,
                )
                from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
                from reportlab.lib import colors
                from reportlab.lib.units import inch

                # Create in-memory PDF buffer
                pdf_buffer = io.BytesIO()

                # Create PDF document
                doc = SimpleDocTemplate(
                    pdf_buffer,
                    pagesize=A4,
                    rightMargin=72,
                    leftMargin=72,
                    topMargin=72,
                    bottomMargin=72,
                )

                # Build PDF content
                story = []
                styles = getSampleStyleSheet()
                title_style = ParagraphStyle(
                    "CustomTitle",
                    parent=styles["Heading1"],
                    fontSize=16,
                    spaceAfter=30,
                )

                # Add title
                title = Paragraph(
                    f"Tax Report: {start_date} to {end_date}", title_style
                )
                story.append(title)
                story.append(Spacer(1, 12))

                # Add summary information
                summary_data = [
                    ["Summary", ""],
                    ["Report Period", f"{start_date} to {end_date}"],
                    ["Total Transactions", str(total_transactions)],
                    ["Symbols Processed", ", ".join(symbols_processed)],
                    ["Total Gain/Loss", f"${float(summary['total_gain_loss']):.2f}"],
                    [
                        "Long Term Gain/Loss",
                        f"${float(summary['total_long_term_gain_loss']):.2f}",
                    ],
                    [
                        "Short Term Gain/Loss",
                        f"${float(summary['total_short_term_gain_loss']):.2f}",
                    ],
                ]

                summary_table = Table(summary_data, colWidths=[2 * inch, 3 * inch])
                summary_table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, 0), 12),
                            ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                            ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                            ("GRID", (0, 0), (-1, -1), 1, colors.black),
                        ]
                    )
                )

                story.append(summary_table)
                story.append(Spacer(1, 24))

                # Add detailed transactions
                story.append(Paragraph("Detailed Transactions", styles["Heading2"]))
                story.append(Spacer(1, 12))

                # Prepare transaction data for table
                transaction_data = [
                    [
                        "Symbol",
                        "Sell Date",
                        "Quantity",
                        "Sell Price",
                        "Purchase Date",
                        "Purchase Price",
                        "Gain/Loss",
                        "Term",
                    ]
                ]

                for symbol in sorted(tax_report.keys()):
                    transactions = tax_report[symbol]
                    for tx in sorted(transactions, key=lambda x: x.sell_date):
                        transaction_data.append(
                            [
                                tx.symbol,
                                tx.sell_date.strftime("%Y-%m-%d"),
                                f"{float(tx.sell_quantity):.2f}",
                                f"${float(tx.sell_price):.2f}",
                                tx.purchase_date.strftime("%Y-%m-%d"),
                                f"${float(tx.purchase_price):.2f}",
                                f"${float(tx.gain_loss):.2f}",
                                "LT" if tx.is_long_term else "ST",
                            ]
                        )

                # Create transactions table
                transactions_table = Table(transaction_data, repeatRows=1)
                transactions_table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, 0), 10),
                            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                            ("FONTSIZE", (0, 1), (-1, -1), 8),
                            ("GRID", (0, 0), (-1, -1), 1, colors.black),
                            # Color code gains/losses
                            (
                                "TEXTCOLOR",
                                (6, 1),
                                (6, -1),
                                colors.green,
                            ),  # Will be overridden for losses
                        ]
                    )
                )

                # Color code negative values in red
                for i, row in enumerate(transaction_data[1:], 1):
                    if row[6].startswith("-") or (row[6].startswith("$-")):
                        transactions_table.setStyle(
                            TableStyle([("TEXTCOLOR", (6, i), (6, i), colors.red)])
                        )

                story.append(transactions_table)

                # Build PDF
                doc.build(story)

                # Get PDF content
                pdf_content = pdf_buffer.getvalue()
                pdf_buffer.close()

                # Generate filename
                pdf_filename = generate_tax_report_filename(
                    start_date, end_date, symbol_list
                ).replace(".csv", ".pdf")

                # Return PDF file
                return Response(
                    content=pdf_content,
                    media_type="application/pdf",
                    headers={
                        "Content-Disposition": f"attachment; filename={pdf_filename}",
                        "Content-Type": "application/pdf",
                    },
                )

            except ImportError:
                raise HTTPException(
                    status_code=status.HTTP_501_NOT_IMPLEMENTED,
                    detail="PDF generation requires reportlab library. Please install it or use 'csv' format.",
                )
            except Exception as pdf_error:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to generate PDF: {str(pdf_error)}",
                )

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate tax report: {str(e)}",
        )


@router.get("/")
async def tax_info():
    """Tax reporting info and available endpoints."""
    return {
        "message": "Tax reporting endpoints",
        "endpoints": {
            "GET /report": "Generate tax report (CSV/PDF). Params: start_date, end_date, symbols, format",
        },
        "methodology": "FIFO (First In First Out)",
        "formats": ["csv", "pdf"],
    }
