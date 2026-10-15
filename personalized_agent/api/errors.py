"""API error type shared by the server and the route handlers."""

from __future__ import annotations


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class Raw:
    """What a route returns when the answer is not JSON: a page, an image.
    Sent as it is, with its own type."""

    def __init__(self, body: bytes, content_type: str) -> None:
        self.body = body
        self.content_type = content_type
