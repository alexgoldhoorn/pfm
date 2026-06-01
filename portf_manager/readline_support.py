"""
Readline support for Portfolio Manager CLI

This module provides command history and line editing functionality
using the Python readline library. It enables:
- Arrow up/down navigation through command history
- Tab completion for commands
- Line editing with common shortcuts (Ctrl-A, Ctrl-E, etc.)
- Persistent command history across sessions
"""

from pathlib import Path
from typing import List, Optional


def setup_readline() -> bool:
    """
    Initialize readline support for the CLI.

    Returns:
        bool: True if readline was successfully initialized, False otherwise
    """
    try:
        pass

        # Configure readline behavior
        configure_readline()

        # Set up history
        setup_history()

        # Set up tab completion
        setup_completion()

        return True

    except ImportError:
        # readline is not available (e.g., on some Windows systems)
        print("⚠️  Readline not available. Command history disabled.")
        return False
    except Exception as e:
        print(f"⚠️  Failed to initialize readline: {e}")
        return False


def configure_readline():
    """Configure readline behavior and key bindings."""
    import readline

    # Set up basic readline configuration
    readline.parse_and_bind("tab: complete")
    readline.parse_and_bind("set editing-mode emacs")

    # Enable case-insensitive completion
    readline.parse_and_bind("set completion-ignore-case on")

    # Show all completions if ambiguous
    readline.parse_and_bind("set show-all-if-ambiguous on")

    # Don't append space after completion
    readline.parse_and_bind("set completion-append-character ''")

    # Set maximum history size
    readline.set_history_length(1000)


def setup_history():
    """Set up persistent command history."""
    import readline

    # Get history file path
    history_file = get_history_file_path()

    try:
        # Load existing history
        if history_file.exists():
            readline.read_history_file(str(history_file))

        # Register cleanup function to save history on exit
        import atexit

        atexit.register(save_history, history_file)

    except Exception as e:
        print(f"⚠️  Could not load command history: {e}")


def setup_completion():
    """Set up tab completion for common commands."""
    import readline

    # Set the completer function
    readline.set_completer(complete_command)

    # Set word delimiters (what constitutes a word boundary)
    readline.set_completer_delims(" \t\n`~!@#$%^&*()=+[{]}\\|;:'\",<>?")


def complete_command(text: str, state: int) -> Optional[str]:
    """
    Tab completion function for CLI commands.

    Args:
        text: The current text being completed
        state: The completion state (0 for first call, 1+ for subsequent)

    Returns:
        Completion suggestion or None if no more completions
    """
    # Define available commands
    commands = [
        # Basic commands
        "help",
        "exit",
        "login",
        "register",
        "paste",
        # Portfolio management
        "list-assets",
        "list-transactions",
        "portfolio-value",
        "list-portfolios",
        "list-entities",
        # Adding data
        "add-asset",
        "add-transaction",
        "add-portfolio",
        "add-entity",
        # Transaction Management
        "update-asset",
        "delete-asset",
        "update-transaction",
        "delete-transaction",
        # Import/Export
        "import-csv",
        "export-transactions",
        # Analytics
        "stock-report",
        # Information
        "list-sectors",
        "show-mapping",
        # Common options
        "--symbol",
        "--amount",
        "--price",
        "--currency",
        "--type",
        "--date",
        "--portfolio",
        "--output",
        "--start-date",
        "--end-date",
        "--news-provider",
        "--exchange",
        "--description",
        "--quantity",
        "--active",
        "--entity",
        "--name",
        "--username",
        "--password",
        # Transaction types
        "buy",
        "sell",
        "dividend",
        "split",
        "transfer_in",
        "transfer_out",
        # Asset types
        "stock",
        "bond",
        "etf",
        "crypto",
        "mutual_fund",
        "commodity",
        "cash",
        # Common symbols (you can extend this)
        "AAPL",
        "GOOGL",
        "MSFT",
        "AMZN",
        "TSLA",
        "META",
        "NVDA",
        "AMD",
        # Currencies
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CAD",
        "AUD",
        "CHF",
    ]

    # Find matches for the current text
    matches = [cmd for cmd in commands if cmd.startswith(text)]

    if state < len(matches):
        return matches[state]
    else:
        return None


def get_history_file_path() -> Path:
    """
    Get the path to the history file.

    Returns:
        Path to the history file
    """
    # Use user's home directory for history file
    home = Path.home()
    return home / ".portf_history"


def save_history(history_file: Path):
    """
    Save command history to file.

    Args:
        history_file: Path to save history to
    """
    try:
        import readline

        # Create directory if it doesn't exist
        history_file.parent.mkdir(parents=True, exist_ok=True)

        # Save history
        readline.write_history_file(str(history_file))

    except Exception:
        # Don't print error message here as it might interfere with exit
        pass


def add_to_history(command: str):
    """
    Add a command to the history manually.

    Args:
        command: Command to add to history
    """
    try:
        import readline

        # Only add non-empty commands
        if command.strip():
            readline.add_history(command.strip())

    except ImportError:
        # readline not available
        pass
    except Exception:
        # Ignore errors
        pass


def get_history() -> List[str]:
    """
    Get the current command history.

    Returns:
        List of commands in history
    """
    try:
        import readline

        history = []
        for i in range(readline.get_current_history_length()):
            history.append(readline.get_history_item(i + 1))

        return history

    except ImportError:
        return []
    except Exception:
        return []


def clear_history():
    """Clear the command history."""
    try:
        import readline

        readline.clear_history()

    except ImportError:
        pass
    except Exception:
        pass


def print_history():
    """Print the command history."""
    try:
        import readline

        history_length = readline.get_current_history_length()

        if history_length == 0:
            print("📋 No command history.")
            return

        print("📋 Command History:")
        print("-" * 50)

        for i in range(history_length):
            command = readline.get_history_item(i + 1)
            if command:
                print(f"{i + 1:3d}: {command}")

        print("-" * 50)
        print(f"Total: {history_length} commands")

    except ImportError:
        print("⚠️  Readline not available. Command history disabled.")
    except Exception as e:
        print(f"❌ Error accessing history: {e}")


# Enhanced input function with readline support
def enhanced_input(prompt: str = "") -> str:
    """
    Enhanced input function with readline support.

    Args:
        prompt: Input prompt to display

    Returns:
        User input string
    """
    try:
        # Use regular input (readline is automatically used if available)
        return input(prompt)

    except KeyboardInterrupt:
        print()
        raise
    except EOFError:
        print()
        raise


def print_readline_help():
    """Print help for readline keyboard shortcuts."""
    print("⌨️  Keyboard Shortcuts:")
    print("-" * 50)
    print("Navigation:")
    print("  ↑/↓ Arrow Keys    - Navigate command history")
    print("  ←/→ Arrow Keys    - Move cursor left/right")
    print("  Ctrl-A            - Move to beginning of line")
    print("  Ctrl-E            - Move to end of line")
    print("  Ctrl-F            - Move forward one character")
    print("  Ctrl-B            - Move backward one character")
    print()
    print("Editing:")
    print("  Ctrl-D            - Delete character under cursor")
    print("  Ctrl-H/Backspace  - Delete character before cursor")
    print("  Ctrl-K            - Delete from cursor to end of line")
    print("  Ctrl-U            - Delete entire line")
    print("  Ctrl-W            - Delete word before cursor")
    print()
    print("History:")
    print("  Ctrl-P            - Previous command (same as ↑)")
    print("  Ctrl-N            - Next command (same as ↓)")
    print("  Ctrl-R            - Reverse search through history")
    print()
    print("Completion:")
    print("  Tab               - Complete command/option")
    print("  Tab Tab           - Show all possible completions")
    print()
    print("Control:")
    print("  Ctrl-C            - Cancel current command")
    print("  Ctrl-D            - Exit (at empty prompt)")
    print("  Enter             - Execute command")
    print("-" * 50)
