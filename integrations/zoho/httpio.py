"""Bounded HTTP I/O: response size cap, JSON parsing, quiet HTTP loggers."""
import json
import logging
from typing import Any

import httpx

from integrations.zoho.errors import ZohoAPIError

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_NOISY_LOGGERS = ("httpx", "httpcore")


def silence_http_loggers() -> None:
    """httpx logs full request URLs at INFO; ours carry search values (PII)."""
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


silence_http_loggers()


async def fetch(http: httpx.AsyncClient, method: str, url: str, what: str,
                **kwargs: Any) -> tuple[int, bytes]:
    """Send one request and read at most MAX_RESPONSE_BYTES; abort the stream beyond that."""
    try:
        async with http.stream(method, url, **kwargs) as response:
            _check_declared_size(response)
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ZohoAPIError("Zoho response too large")
                chunks.append(chunk)
            return response.status_code, b"".join(chunks)
    except httpx.HTTPError as exc:
        raise ZohoAPIError(f"Zoho {what} failed ({type(exc).__name__})") from None


def _check_declared_size(response: httpx.Response) -> None:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise ZohoAPIError("Zoho response too large")


def parse_json(body: bytes) -> Any:
    """Parsed JSON, or None when the body is empty, malformed or absurdly nested."""
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        return None
