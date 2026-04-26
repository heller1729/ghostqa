"""
GhostQA Logging System

Structured logging with Rich formatting for terminal output.
"""

import logging
from rich.console import Console
from rich.logging import RichHandler


console = Console()


def setup_logger(name: str = "ghostqa", debug: bool = False) -> logging.Logger:
    """
    Create a structured logger with Rich formatting.

    Args:
        name: Logger name
        debug: Enable DEBUG level logging

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    level = logging.DEBUG if debug else logging.INFO
    logger.setLevel(level)

    handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    handler.setLevel(level)

    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logger.addHandler(handler)

    return logger
