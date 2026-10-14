"""cc-chronicle: archive Claude Code memory and sessions to any rclone remote."""

__version__ = "0.1.0"


class ChronicleError(RuntimeError):
    """An error worth showing to the user as one line, without a traceback."""
