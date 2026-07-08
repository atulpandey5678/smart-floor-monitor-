"""Pagination utilities for list endpoints.

Provides a PaginationParams model and a helper function to build
paginated response dicts with metadata.

Requirements: 20.2, 20.3
"""

import math
from typing import Any, List, Optional


class PaginationParams:
    """Offset-based pagination parameters.

    Attributes:
        page: Current page number (1-indexed, default 1).
        page_size: Number of items per page (default 20, max 100).
    """

    def __init__(self, page: int = 1, page_size: int = 20):
        self.page = max(1, page)
        self.page_size = max(1, min(100, page_size))

    @property
    def offset(self) -> int:
        """Calculate SQL OFFSET from page and page_size."""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """SQL LIMIT value (same as page_size)."""
        return self.page_size


def paginated_response(
    items: List[Any],
    total_count: int,
    page: int,
    page_size: int,
) -> dict:
    """Build a paginated response dict with metadata.

    Args:
        items: The list of items for the current page.
        total_count: Total number of items across all pages.
        page: Current page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        Dict with keys: data, total, page, page_size, total_pages.
    """
    total_pages = math.ceil(total_count / page_size) if page_size > 0 else 0
    return {
        "data": items,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }
