"""Tests for API pagination utilities and endpoint integration.

Validates Requirements 20.2 and 20.3:
- Offset-based pagination with default page_size=20, max=100
- Pagination metadata: total_count, page, page_size, total_pages
"""

import math
import pytest
from api.pagination import PaginationParams, paginated_response


class TestPaginationParams:
    """Tests for PaginationParams utility class."""

    def test_default_values(self):
        params = PaginationParams()
        assert params.page == 1
        assert params.page_size == 20

    def test_custom_values(self):
        params = PaginationParams(page=3, page_size=50)
        assert params.page == 3
        assert params.page_size == 50

    def test_offset_calculation(self):
        params = PaginationParams(page=1, page_size=20)
        assert params.offset == 0

        params = PaginationParams(page=2, page_size=20)
        assert params.offset == 20

        params = PaginationParams(page=3, page_size=10)
        assert params.offset == 20

    def test_limit_equals_page_size(self):
        params = PaginationParams(page=1, page_size=50)
        assert params.limit == 50

    def test_page_minimum_is_1(self):
        params = PaginationParams(page=0, page_size=20)
        assert params.page == 1
        assert params.offset == 0

        params = PaginationParams(page=-5, page_size=20)
        assert params.page == 1

    def test_page_size_clamped_to_max_100(self):
        params = PaginationParams(page=1, page_size=200)
        assert params.page_size == 100

    def test_page_size_minimum_is_1(self):
        params = PaginationParams(page=1, page_size=0)
        assert params.page_size == 1

        params = PaginationParams(page=1, page_size=-5)
        assert params.page_size == 1


class TestPaginatedResponse:
    """Tests for paginated_response helper function."""

    def test_basic_response_structure(self):
        items = [{"id": 1}, {"id": 2}]
        result = paginated_response(items, total_count=50, page=1, page_size=20)

        assert result["data"] == items
        assert result["total"] == 50
        assert result["page"] == 1
        assert result["page_size"] == 20
        assert result["total_pages"] == 3  # ceil(50/20) = 3

    def test_total_pages_calculation(self):
        result = paginated_response([], total_count=100, page=1, page_size=20)
        assert result["total_pages"] == 5

        result = paginated_response([], total_count=101, page=1, page_size=20)
        assert result["total_pages"] == 6  # ceil(101/20) = 6

        result = paginated_response([], total_count=0, page=1, page_size=20)
        assert result["total_pages"] == 0

    def test_single_page(self):
        items = [{"id": i} for i in range(5)]
        result = paginated_response(items, total_count=5, page=1, page_size=20)

        assert result["total_pages"] == 1
        assert len(result["data"]) == 5

    def test_empty_results(self):
        result = paginated_response([], total_count=0, page=1, page_size=20)

        assert result["data"] == []
        assert result["total"] == 0
        assert result["total_pages"] == 0

    def test_last_page_partial(self):
        # 3 items on the last page of 23 total with page_size=10
        items = [{"id": 21}, {"id": 22}, {"id": 23}]
        result = paginated_response(items, total_count=23, page=3, page_size=10)

        assert result["total_pages"] == 3
        assert result["page"] == 3
        assert len(result["data"]) == 3
