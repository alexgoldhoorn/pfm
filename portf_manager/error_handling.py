"""
Error handling module for Portfolio Manager CLI

Provides comprehensive error handling with proper exit codes and debug support.
"""

import sys
import traceback
import os
from datetime import datetime
from typing import Any
from errno import ENOSPC, EACCES, EPERM


class ExitCodes:
    """Standard exit codes for the application."""

    SUCCESS = 0
    GENERAL_ERROR = 1
    AUTHENTICATION_ERROR = 2
    FILE_NOT_FOUND = 3
    PERMISSION_ERROR = 4
    INVALID_INPUT = 5
    DISK_FULL = 6
    UNKNOWN_SYMBOL = 7
    INVALID_DATE = 8
    NETWORK_ERROR = 9
    DATABASE_ERROR = 10


class PortfolioManagerError(Exception):
    """Base exception class for Portfolio Manager errors."""

    def __init__(self, message: str, exit_code: int = ExitCodes.GENERAL_ERROR):
        super().__init__(message)
        self.exit_code = exit_code


class FileIOError(PortfolioManagerError):
    """Error related to file I/O operations."""

    def __init__(
        self,
        message: str,
        filename: str = None,
        exit_code: int = ExitCodes.GENERAL_ERROR,
    ):
        super().__init__(message, exit_code)
        self.filename = filename


class PermissionError(FileIOError):
    """Error related to file permissions."""

    def __init__(self, message: str, filename: str = None):
        super().__init__(message, filename, ExitCodes.PERMISSION_ERROR)


class DiskFullError(FileIOError):
    """Error when disk is full."""

    def __init__(self, message: str, filename: str = None):
        super().__init__(message, filename, ExitCodes.DISK_FULL)


class InvalidDateError(PortfolioManagerError):
    """Error for invalid date formats."""

    def __init__(self, message: str, date_str: str = None):
        super().__init__(message, ExitCodes.INVALID_DATE)
        self.date_str = date_str


class UnknownSymbolError(PortfolioManagerError):
    """Error for unknown or invalid symbols."""

    def __init__(self, message: str, symbol: str = None):
        super().__init__(message, ExitCodes.UNKNOWN_SYMBOL)
        self.symbol = symbol


class ErrorHandler:
    """Central error handler with debug mode support."""

    def __init__(self, debug: bool = False):
        self.debug = debug

    def handle_error(self, error: Exception, context: str = None) -> None:
        """Handle an error with appropriate logging and exit."""
        if isinstance(error, PortfolioManagerError):
            exit_code = error.exit_code
        else:
            exit_code = ExitCodes.GENERAL_ERROR

        # Print user-friendly error message
        if context:
            print(f"❌ Error in {context}: {error}")
        else:
            print(f"❌ Error: {error}")

        # Print debug information if debug mode is enabled
        if self.debug:
            print("\n🔍 Debug information:")
            print(f"Error type: {type(error).__name__}")
            print(f"Exit code: {exit_code}")
            print("\nStack trace:")
            traceback.print_exc()

        sys.exit(exit_code)

    def handle_file_error(
        self, error: Exception, filename: str = None, operation: str = None
    ) -> None:
        """Handle file-related errors with specific messaging."""
        if isinstance(error, OSError):
            if error.errno == ENOSPC:
                disk_error = DiskFullError(
                    f"Disk full while {operation or 'working with'} file: {filename or 'unknown'}",
                    filename,
                )
                self.handle_error(disk_error, f"file operation ({operation})")
            elif error.errno in (EACCES, EPERM):
                perm_error = PermissionError(
                    f"Permission denied for {operation or 'accessing'} file: {filename or 'unknown'}",
                    filename,
                )
                self.handle_error(perm_error, f"file operation ({operation})")
            else:
                file_error = FileIOError(
                    f"File I/O error while {operation or 'working with'} file: {filename or 'unknown'} - {error}",
                    filename,
                    (
                        ExitCodes.FILE_NOT_FOUND
                        if error.errno == 2
                        else ExitCodes.GENERAL_ERROR
                    ),
                )
                self.handle_error(file_error, f"file operation ({operation})")
        else:
            # Generic file error
            file_error = FileIOError(
                f"File error while {operation or 'working with'} file: {filename or 'unknown'} - {error}",
                filename,
            )
            self.handle_error(file_error, f"file operation ({operation})")

    def handle_date_error(
        self, error: ValueError, date_str: str = None, context: str = None
    ) -> None:
        """Handle date parsing errors."""
        date_error = InvalidDateError(
            f"Invalid date format: {date_str or 'unknown'} - {error}", date_str
        )
        self.handle_error(date_error, context or "date parsing")

    def handle_symbol_warning(self, symbol: str, context: str = None) -> None:
        """Handle unknown symbol warnings (non-fatal)."""
        if context:
            print(
                f"⚠️  Warning in {context}: Unknown symbol '{symbol}' - using default sector"
            )
        else:
            print(f"⚠️  Warning: Unknown symbol '{symbol}' - using default sector")

    def validate_date_format(self, date_str: str, context: str = None) -> datetime:
        """Validate and parse date string."""
        try:
            # Try YYYY-MM-DD format first
            return datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            try:
                # Try DD/MM/YYYY format
                return datetime.strptime(date_str, "%d/%m/%Y")
            except ValueError:
                try:
                    # Try MM/DD/YYYY format
                    return datetime.strptime(date_str, "%m/%d/%Y")
                except ValueError as e:
                    self.handle_date_error(e, date_str, context)

    def convert_date_format(self, date_str: str, context: str = None) -> str:
        """Convert date string to YYYY-MM-DD format with proper error handling."""
        try:
            # Try DD/MM/YYYY format first
            if "/" in date_str:
                parts = date_str.split("/")
                if len(parts) == 3:
                    day, month, year = parts
                    # Validate numeric values
                    day_int = int(day)
                    month_int = int(month)
                    year_int = int(year)

                    # Basic validation
                    if not (1 <= day_int <= 31):
                        raise ValueError(f"Invalid day: {day}")
                    if not (1 <= month_int <= 12):
                        raise ValueError(f"Invalid month: {month}")
                    if not (1900 <= year_int <= 2100):
                        raise ValueError(f"Invalid year: {year}")

                    return f"{year.zfill(4)}-{month.zfill(2)}-{day.zfill(2)}"

            # If it's already in YYYY-MM-DD format, validate it
            datetime.strptime(date_str, "%Y-%m-%d")
            return date_str

        except ValueError as e:
            self.handle_date_error(e, date_str, context)

    def safe_file_operation(
        self,
        operation: callable,
        filename: str = None,
        operation_name: str = None,
        *args,
        **kwargs,
    ) -> Any:
        """Safely execute a file operation with error handling."""
        try:
            return operation(*args, **kwargs)
        except Exception as e:
            self.handle_file_error(e, filename, operation_name)

    def check_file_permissions(self, filepath: str, operation: str = "access") -> bool:
        """Check if file can be accessed with proper error handling."""
        try:
            if operation == "read":
                return os.access(filepath, os.R_OK)
            elif operation == "write":
                if os.path.exists(filepath):
                    return os.access(filepath, os.W_OK)
                else:
                    # Check if parent directory is writable
                    parent_dir = os.path.dirname(filepath)
                    return os.access(parent_dir, os.W_OK)
            else:
                return os.access(filepath, os.F_OK)
        except Exception as e:
            self.handle_file_error(e, filepath, f"checking {operation} permissions")
            return False

    def ensure_directory_exists(self, directory: str) -> None:
        """Ensure directory exists with proper error handling."""
        try:
            os.makedirs(directory, exist_ok=True)
        except Exception as e:
            self.handle_file_error(e, directory, "creating directory")


def create_error_handler(debug: bool = False) -> ErrorHandler:
    """Create an error handler instance."""
    return ErrorHandler(debug=debug)


def handle_unknown_symbol(symbol: str, context: str = None, debug: bool = False) -> str:
    """Handle unknown symbol with warning and return default sector."""
    handler = create_error_handler(debug)
    handler.handle_symbol_warning(symbol, context)
    return "Unknown Sector"  # Default sector for unknown symbols
