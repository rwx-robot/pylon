"""
pylon.client - Async HTTP client with middleware, retry, and service discovery.

Example:
    from pylon.client import Client

    client = Client(base_url="http://localhost:8000")

    @client.middleware
    async def auth_middleware(req, next):
        req.headers["Authorization"] = "Bearer token"
        return await next(req)

    response = await client.get("/users/1")
    print(response.json())

    # With service discovery:
    from pylon.client import Client, ServiceDiscovery

    class MyServiceDiscovery(ServiceDiscovery):
        async def resolve(self, service: str) -> str:
            return f"http://{service}:8000"

    client = Client(base_url="http://localhost:8000", discovery=MyServiceDiscovery())
    response = await client.get("/users/1")
"""

from __future__ import annotations

import asyncio
import json
import time
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Union
from urllib.parse import urljoin

import httpx

from pylon.exceptions import PylonError


# ---------------------------------------------------------------
# Service Discovery
# ---------------------------------------------------------------

class ServiceDiscovery(Protocol):
    """Protocol for service discovery implementations."""

    async def resolve(self, service: str) -> str:
        """Resolve a service name to a base URL."""
        ...


class StaticServiceDiscovery(ServiceDiscovery):
    """Simple static service discovery with a registry."""

    def __init__(self, registry: Optional[Dict[str, str]] = None):
        self._registry: Dict[str, str] = registry or {}

    async def resolve(self, service: str) -> str:
        if service not in self._registry:
            raise PylonClientError(f"Unknown service: {service}")
        return self._registry[service]

    def register(self, service: str, url: str) -> None:
        self._registry[service] = url


# ---------------------------------------------------------------
# Client Request / Response (mirrors server-side Protocol)
# ---------------------------------------------------------------

@dataclass
class ClientRequest:
    """HTTP request for the client."""
    method: str
    path: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[bytes] = None
    timeout: Optional[float] = None

    def copy(self) -> ClientRequest:
        return ClientRequest(
            method=self.method,
            path=self.path,
            headers=dict(self.headers),
            body=self.body,
            timeout=self.timeout,
        )


@dataclass
class ClientResponse:
    """HTTP response from the client."""
    status_code: int
    headers: Dict[str, str]
    body: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        import json
        return json.loads(self.body.decode("utf-8"))

    def text(self) -> str:
        return self.body.decode("utf-8")


# ---------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------

# Middleware signature: (ClientRequest, next_handler) -> Awaitable[ClientResponse]
# next_handler: () -> Awaitable[ClientResponse]
ClientMiddleware = Callable[[ClientRequest, Callable[[], Awaitable[ClientResponse]]], Awaitable[ClientResponse]]


# ---------------------------------------------------------------
# Retry Strategy
# ---------------------------------------------------------------

@dataclass
class RetryStrategy:
    """Configurable retry strategy with exponential backoff."""
    max_attempts: int = 3
    initial_delay: float = 0.5
    max_delay: float = 30.0
    backoff_multiplier: float = 2.0
    retry_on_status: tuple = (429, 500, 502, 503, 504)

    def delay(self, attempt: int) -> float:
        """Calculate delay for a given attempt number."""
        delay = self.initial_delay * (self.backoff_multiplier ** (attempt - 1))
        return min(delay, self.max_delay)


class DefaultRetryStrategy(RetryStrategy):
    """Default retry strategy: 3 attempts with exponential backoff."""
    pass


# ---------------------------------------------------------------
# Client Errors
# ---------------------------------------------------------------

class PylonClientError(PylonError):
    """Base exception for client errors."""
    pass


class ClientConnectionError(PylonClientError):
    """Raised when connection to the server fails."""
    pass


class ClientTimeoutError(PylonClientError):
    """Raised when a request times out."""
    pass


class ClientResponseError(PylonClientError):
    """Raised when response indicates an error status."""
    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}")


# ---------------------------------------------------------------
# Main Client
# ---------------------------------------------------------------

class Client:
    """
    Async HTTP client with middleware chain, retry, and service discovery.

    Example:
        client = Client(base_url="http://localhost:8000")

        # Add middleware
        @client.middleware
        async def auth(req, next):
            req.headers["Authorization"] = "Bearer token"
            return await next(req)

        # Make requests
        resp = await client.get("/users/1")
        print(resp.json())

        resp = await client.post("/users", json={"name": "Alice"})
        print(resp.json())
    """

    def __init__(
        self,
        base_url: str = "",
        timeout: float = 30.0,
        retry: Optional[RetryStrategy] = None,
        discovery: Optional[ServiceDiscovery] = None,
        middleware: Optional[List[ClientMiddleware]] = None,
        follow_redirects: bool = True,
        max_keepalive_connections: int = 20,
        http2: bool = False,
    ):
        self._base_url = base_url.rstrip("/")
        self._default_timeout = timeout
        self._retry = retry or DefaultRetryStrategy()
        self._discovery = discovery
        self._middleware: List[ClientMiddleware] = list(middleware) if middleware else []
        self._follow_redirects = follow_redirects
        self._http2 = http2
        self._max_keepalive = max_keepalive_connections
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def use(self, middleware: ClientMiddleware) -> None:
        """Register a client middleware."""
        self._middleware.append(middleware)

    def middleware(self, func: ClientMiddleware) -> ClientMiddleware:
        """Decorator to register a middleware."""
        self._middleware.append(func)
        return func

    async def get(
        self,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> ClientResponse:
        return await self.request("GET", path, headers=headers, params=params, timeout=timeout)

    async def post(
        self,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        json_body: Optional[Any] = None,
        data: Optional[Any] = None,
        timeout: Optional[float] = None,
    ) -> ClientResponse:
        body: Optional[bytes] = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers = dict(headers or {})
            headers.setdefault("Content-Type", "application/json")
        return await self.request("POST", path, headers=headers, body=body, timeout=timeout)

    async def put(
        self,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        json_body: Optional[Any] = None,
        data: Optional[Any] = None,
        timeout: Optional[float] = None,
    ) -> ClientResponse:
        body: Optional[bytes] = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers = dict(headers or {})
            headers.setdefault("Content-Type", "application/json")
        return await self.request("PUT", path, headers=headers, body=body, timeout=timeout)

    async def patch(
        self,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        json_body: Optional[Any] = None,
        data: Optional[Any] = None,
        timeout: Optional[float] = None,
    ) -> ClientResponse:
        body: Optional[bytes] = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers = dict(headers or {})
            headers.setdefault("Content-Type", "application/json")
        return await self.request("PATCH", path, headers=headers, body=body, timeout=timeout)

    async def delete(
        self,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> ClientResponse:
        return await self.request("DELETE", path, headers=headers, timeout=timeout)

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> ClientResponse:
        """Make an HTTP request with middleware and retry support."""
        # Resolve service if needed
        if self._discovery and path.startswith("service://"):
            service_name = path.split("://", 1)[1].split("/", 1)[0]
            base = await self._discovery.resolve(service_name)
            remaining_path = "/" + path.split("://", 1)[1].split("/", 1)[1]
            actual_url = base + remaining_path
        else:
            actual_url = urljoin(self._base_url + "/", path.lstrip("/"))

        # Apply timeout
        req_timeout = timeout if timeout is not None else self._default_timeout

        # Build client request
        req = ClientRequest(
            method=method.upper(),
            path=actual_url,
            headers=dict(headers) if headers else {},
            body=body,
            timeout=req_timeout,
        )

        # Build the middleware chain: each middleware wraps the next.
        # Each link receives (req, call_next) where call_next() -> ClientResponse
        async def make_httpx_request(r: ClientRequest) -> ClientResponse:
            return await self._do_request(r)

        handler: Callable[[ClientRequest], Awaitable[ClientResponse]] = make_httpx_request
        for mw in reversed(self._middleware):
            next_handler = handler

            async def wrapped(r: ClientRequest, _mw=mw, _next=next_handler) -> ClientResponse:
                async def call_next() -> ClientResponse:
                    return await _next(r)
                return await _mw(r, call_next)
            handler = wrapped

        # Retry loop
        last_error: Exception = ClientConnectionError("Connection failed")
        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                req_copy = req.copy()
                return await handler(req_copy)
            except (ClientConnectionError, ClientTimeoutError, httpx.HTTPError) as exc:
                last_error = exc
                if attempt < self._retry.max_attempts:
                    delay = self._retry.delay(attempt)
                    await asyncio.sleep(delay)
                else:
                    raise ClientConnectionError(f"All {self._retry.max_attempts} attempts failed: {exc}") from exc

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _get_httpx_client(self) -> httpx.AsyncClient:
        """Get or create the underlying httpx client."""
        if self._client is None:
            limits = httpx.Limits(
                max_keepalive_connections=self._max_keepalive,
                max_connections=100,
            )
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._default_timeout),
                limits=limits,
                http2=self._http2,
                follow_redirects=self._follow_redirects,
            )
        return self._client

    async def _do_request(self, req: ClientRequest) -> ClientResponse:
        """Execute a single HTTP request via httpx."""
        client = await self._get_httpx_client()
        try:
            response = await client.request(
                method=req.method,
                url=req.path,
                headers=req.headers,
                content=req.body,
                timeout=req.timeout,
            )
            return ClientResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                body=response.content,
            )
        except httpx.TimeoutException as exc:
            raise ClientTimeoutError(f"Request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise ClientConnectionError(f"Connection failed: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise ClientResponseError(exc.response.status_code, exc.response.content) from exc
        except httpx.HTTPError as exc:
            raise ClientConnectionError(f"HTTP error: {exc}") from exc

    # Context manager support
    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


# Convenience function for one-off requests
async def request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json: Optional[Any] = None,
    timeout: float = 30.0,
) -> ClientResponse:
    """Make a one-off async HTTP request."""
    client = Client(base_url=url, timeout=timeout)
    if json is not None:
        return await client.request(method, "/", headers=headers, json=json)
    return await client.request(method, "/", headers=headers)
