from __future__ import annotations


class InvalidImageError(Exception):
    """Raised when an image file cannot be decoded (corrupted or spoofed content)."""


class InvalidFileError(Exception):
    """Raised when a non-image file (e.g. PDF) is invalid or exceeds limits."""
