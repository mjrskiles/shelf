"""shelf — a catalog and full-text index for datasheets and manuals."""

__version__ = "0.1.0"


class ShelfError(Exception):
    """User-facing error. The CLI prints the message and exits 1."""
