"""pylon.middleware - Middleware components and chain execution."""

from __future__ import annotations

import asyncio
import time
import sys
import traceback
from typing import Any, Awaitable, Callable, List, Optional, Protocol

from pylon.protocol import Request, Response


# Middleware callable signature
MiddlewareNext = Callable[[], Awaitable[None]]
MiddlewareHandler = Callable[[Request, Response, MiddlewareNext], Awaitable[None]]


class BaseMiddleware:
    """
    Base class for middleware.

    Subclass and override the `dispatch` method.

    Example:
        class LoggingMiddleware(BaseMiddleware):
            async def dispatch(self, req, res, next_):
                print(f"Before: {req.method} {req.path}")
                await next_()
                print(f"After: {res.status_code}")
    """

    async def dispatch(
        self,
        req: Request,
        res: Response,
        next_: MiddlewareNext,
    ) -> None:
        """Process the request and call the next middleware/handler."""
        await next_()


class RecoveryMiddleware(BaseMiddleware):
    """
    Recovery middleware that catches exceptions and returns a 500 response.

    This prevents unhandled exceptions from crashing the server and
    provides a consistent error response format.
    """

    def __init__(
        self,
        *,
        debug: bool = False,
        on_error: Optional[Callable[[Exception, Request, Response], None]] = None,
    ):
        self.debug = debug
        self.on_error = on_error

    async def dispatch(
        self,
        req: Request,
        res: Response,
        next_: MiddlewareNext,
    ) -> None:
        try:
            await next_()
        except Exception as exc:  # noqa: BLE001
            # Call custom error handler if provided
            if self.on_error:
                self.on_error(exc, req, res)

            # Log the error
            error_msg = "".join(traceback.format_exception(*sys.exc_info()))
            if self.debug:
                print(f"[RecoveryMiddleware] Error: {error_msg}", file=sys.stderr)
            else:
                print(f"[RecoveryMiddleware] Error: {exc}", file=sys.stderr)

            # Set 500 response
            res.status_code = 500
            if self.debug:
                res.json({
                    "error": "Internal Server Error",
                    "detail": str(exc),
                    "trace": error_msg,
                })
            else:
                res.json({
                    "error": "Internal Server Error",
                    "detail": "An unexpected error occurred",
                })


class LoggerMiddleware(BaseMiddleware):
    """
    Logger middleware that logs request/response information.

    Logs: method, path, status_code, and duration.
    """

    def __init__(self, logger: Optional[Callable[[str], None]] = None):
        self.logger = logger or print
        self._log = self.logger

    async def dispatch(
        self,
        req: Request,
        res: Response,
        next_: MiddlewareNext,
    ) -> None:
        import time

        start = time.perf_counter()
        method = req.method
        path = req.path

        await next_()

        duration = time.perf_counter() - start
        self._log(
            f"[{method}] {path} - {res.status_code} ({duration*1000:.2f}ms)"
        )


class RequestIDMiddleware(BaseMiddleware):
    """
    Middleware that adds a unique request ID to each request.

    The ID is stored in req.headers["X-Request-ID"] and also
    added to the response headers.
    """

    def __init__(self, header_name: str = "X-Request-ID"):
        self.header_name = header_name

    async def dispatch(
        self,
        req: Request,
        res: Response,
        next_: MiddlewareNext,
    ) -> None:
        import uuid

        request_id = req.headers.get(self.header_name) or str(uuid.uuid4())[:8]
        req.headers[self.header_name] = request_id
        res.set_header(self.header_name, request_id)

        await next_()


class BindingMiddleware(BaseMiddleware):
    """
    Middleware that pre-parses request body as JSON and stores it on the request.

    After this middleware runs, the parsed JSON body is available as
    ``req.json_data``. This avoids re-parsing the body when ``bind()`` is called.

    If the body is not valid JSON, ``req.json_data`` is set to a sentinel
    value and a BindingError will be raised when ``bind()`` is called.

    Example:
        app.use(BindingMiddleware())

        @app.post("/users")
        async def create_user(req, res):
            # bind() uses req.body directly, no double-parsing
            user = await bind(req, UserCreate)
            res.json({"user": user.model_dump()})
    """

    async def dispatch(
        self,
        req: Request,
        res: Response,
        next_: MiddlewareNext,
    ) -> None:
        import json as _json

        # Pre-parse body if present
        req.json_data = None
        if req.body:
            try:
                req.json_data = _json.loads(req.body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Not valid JSON - bind() will raise BindingError
                pass

        await next_()


class CORSMiddleware(BaseMiddleware):
    """
    CORS (Cross-Origin Resource Sharing) middleware.

    Handles OPTIONS preflight requests and adds CORS headers to responses.
    """

    def __init__(
        self,
        allow_origins: Optional[List[str]] = None,
        allow_methods: Optional[List[str]] = None,
        allow_headers: Optional[List[str]] = None,
        allow_credentials: bool = False,
        max_age: int = 86400,
    ):
        self.allow_origins = allow_origins or ["*"]
        self.allow_methods = allow_methods or ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
        self.allow_headers = allow_headers or ["*"]
        self.allow_credentials = allow_credentials
        self.max_age = max_age

    async def dispatch(
        self,
        req: Request,
        res: Response,
        next_: MiddlewareNext,
    ) -> None:
        origin = req.headers.get("Origin", "*")

        # Set CORS headers
        if self.allow_origins == ["*"] or origin in self.allow_origins:
            res.set_header("Access-Control-Allow-Origin", origin)
        elif self.allow_origins:
            res.set_header("Access-Control-Allow-Origin", self.allow_origins[0])

        if self.allow_credentials:
            res.set_header("Access-Control-Allow-Credentials", "true")

        res.set_header("Access-Control-Allow-Methods", ", ".join(self.allow_methods))
        res.set_header("Access-Control-Allow-Headers", ", ".join(self.allow_headers))
        res.set_header("Access-Control-Max-Age", str(self.max_age))

        # Handle preflight
        if req.method == "OPTIONS":
            res.status_code = 204
            return

        await next_()


def middleware_chain(
    middlewares: List[MiddlewareHandler],
    final_handler: Callable[[], Awaitable[None]],
) -> Callable[[], Awaitable[None]]:
    """
    Build a middleware chain from a list of middlewares.

    Returns a single callable that executes all middlewares in order,
    finally calling the handler.
    """
    async def chain() -> None:
        async def recursive(index: int) -> None:
            if index >= len(middlewares):
                await final_handler()
                return

            middleware = middlewares[index]

            async def next_() -> None:
                await recursive(index + 1)

            # Middleware expects (req, res, next_)
            # We need to create a closure that captures req and res
            # This is handled by the Application class
            await middleware(req=None, res=None, next_=next_)  # type: ignore

    return chain


# Alias for backwards compatibility
Next = MiddlewareNext


# ============================================================
# Security Middlewares (v3.0.0)
# ============================================================


class RateLimitMiddleware(BaseMiddleware):
    """
    Sliding-window rate limiting middleware.

    Limits requests per client IP (or custom key) within a time window.
    Returns 429 Too Many Requests when the limit is exceeded.

    Example:
        # 100 requests per 60 seconds per IP
        app.use(RateLimitMiddleware(limit=100, window=60.0))

        # Custom key function (e.g., by API token header)
        app.use(RateLimitMiddleware(limit=1000, window=60.0,
                                   key_func=lambda req: req.headers.get("X-API-Key", "anonymous")))

        # Custom response when rate limited
        def my_on_limit(req, res, retry_after):
            res.status_code = 429
            res.headers["Retry-After"] = str(int(retry_after))
            res.json({"error": "Rate limit exceeded", "retry_after": retry_after})

        app.use(RateLimitMiddleware(limit=10, window=1.0, on_limit=my_on_limit))
    """

    def __init__(
        self,
        limit: int = 100,
        window: float = 60.0,
        key_func: Optional[Callable[[Request], str]] = None,
        on_limit: Optional[Callable[[Request, Response, float], None]] = None,
    ):
        """
        Args:
            limit: Maximum requests allowed within the window.
            window: Time window in seconds.
            key_func: Function to extract the rate-limit key from a request.
                      Defaults to client_ip (or "anonymous" if not available).
            on_limit: Callback invoked when rate limit is exceeded.
                      Receives (req, res, retry_after_seconds).
                      Default: sets 429 with JSON error and Retry-After header.
        """
        self.limit = limit
        self.window = window
        self.key_func = key_func or (lambda req: req.client_ip or "anonymous")
        self.on_limit = on_limit
        # Timestamps of recent requests per key: list of (timestamp,)
        self._requests: dict[str, list[float]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    def _cleanup(self, key: str, now: float) -> None:
        """Remove expired timestamps outside the window."""
        cutoff = now - self.window
        self._requests[key] = [t for t in self._requests.get(key, []) if t > cutoff]

    async def _is_allowed(self, key: str, now: float) -> tuple[bool, float]:
        """
        Check if a request is allowed and record it.

        Returns (allowed, retry_after).
        """
        async with self._lock:
            self._cleanup(key, now)
            timestamps = self._requests

            if len(timestamps.get(key, [])) < self.limit:
                timestamps.setdefault(key, []).append(now)
                return True, 0.0
            else:
                # Oldest request in window
                oldest = min(timestamps[key])
                retry_after = oldest + self.window - now
                return False, max(0.0, retry_after)

    async def dispatch(
        self,
        req: Request,
        res: Response,
        next_: MiddlewareNext,
    ) -> None:
        import time
        now = time.time()
        key = self.key_func(req)
        allowed, retry_after = await self._is_allowed(key, now)

        if not allowed:
            res.headers["X-RateLimit-Limit"] = str(self.limit)
            res.headers["X-RateLimit-Remaining"] = "0"
            res.headers["X-RateLimit-Reset"] = str(int(now + retry_after))
            res.headers["Retry-After"] = str(int(retry_after) + 1)

            if self.on_limit:
                self.on_limit(req, res, retry_after)
            else:
                res.status_code = 429
                res.json({
                    "error": "Too Many Requests",
                    "message": f"Rate limit exceeded. Retry after {retry_after:.1f}s.",
                    "retry_after": round(retry_after, 1),
                })
            return

        await next_()

        # Add rate limit headers on success too
        async with self._lock:
            remaining = self.limit - len(self._requests.get(key, []))
        res.headers["X-RateLimit-Limit"] = str(self.limit)
        res.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        res.headers["X-RateLimit-Reset"] = str(int(now + self.window))


class SecurityHeadersMiddleware(BaseMiddleware):
    """
    Adds security-related HTTP response headers.

    Adds a comprehensive set of security headers that help protect
    against common web vulnerabilities (XSS, clickjacking, MIME sniffing, etc.).

    Headers added:
    - Strict-Transport-Security (HSTS)
    - X-Content-Type-Options: nosniff
    - X-Frame-Options
    - X-XSS-Protection
    - Content-Security-Policy
    - Referrer-Policy
    - Permissions-Policy

    Example:
        app.use(SecurityHeadersMiddleware())

        # Custom CSP
        app.use(SecurityHeadersMiddleware(
            csp="default-src 'self'; script-src 'self' 'unsafe-inline';"
        ))

        # Custom X-Frame-Options value
        app.use(SecurityHeadersMiddleware(
            x_frame_options="SAMEORIGIN"
        ))
    """

    def __init__(
        self,
        hsts_max_age: int = 31536000,  # 1 year
        frame_deny: bool = True,
        x_frame_options: Optional[str] = None,
        content_type_nosniff: bool = True,
        xss_protection: bool = True,
        referrer_policy: str = "strict-origin-when-cross-origin",
        csp: Optional[str] = None,
        permissions_policy: Optional[str] = None,
    ):
        self.hsts_max_age = hsts_max_age
        self.frame_deny = frame_deny
        self.x_frame_options = x_frame_options
        self.content_type_nosniff = content_type_nosniff
        self.xss_protection = xss_protection
        self.referrer_policy = referrer_policy
        self.csp = csp or "default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self';"
        self.permissions_policy = permissions_policy or "geolocation=(), microphone=(), camera=()"

    async def dispatch(
        self,
        req: Request,
        res: Response,
        next_: MiddlewareNext,
    ) -> None:
        await next_()

        # Always add these regardless of response status
        if self.content_type_nosniff:
            res.set_header("X-Content-Type-Options", "nosniff")

        if self.frame_deny:
            res.set_header("X-Frame-Options", self.x_frame_options or "DENY")

        if self.xss_protection:
            res.set_header("X-XSS-Protection", "1; mode=block")

        res.set_header("Referrer-Policy", self.referrer_policy)

        if self.hsts_max_age > 0:
            res.set_header(
                "Strict-Transport-Security",
                f"max-age={self.hsts_max_age}; includeSubDomains"
            )

        if self.csp:
            res.set_header("Content-Security-Policy", self.csp)

        if self.permissions_policy:
            res.set_header("Permissions-Policy", self.permissions_policy)


class IPAllowlistMiddleware(BaseMiddleware):
    """
    IP allowlist / blocklist middleware.

    Allows or denies requests based on client IP address.

    Example:
        # Allow only specific IPs
        app.use(IPAllowlistMiddleware(allow=["127.0.0.1", "::1"], mode="allow"))

        # Deny known bad IPs
        app.use(IPAllowlistMiddleware(deny=["192.168.1.100"], mode="deny"))

        # Custom handler when blocked
        def blocked_handler(req, res, client_ip):
            res.status_code = 403
            res.json({"error": "Access denied", "ip": client_ip})

        app.use(IPAllowlistMiddleware(allow=["10.0.0.0/8"], mode="allow",
                                     on_blocked=blocked_handler))
    """

    def __init__(
        self,
        allow: Optional[List[str]] = None,
        deny: Optional[List[str]] = None,
        mode: str = "allow",
        on_blocked: Optional[Callable[[Request, Response, str], None]] = None,
    ):
        """
        Args:
            allow: List of allowed IP addresses or CIDR ranges (e.g., "192.168.1.0/24").
            deny: List of denied IP addresses or CIDR ranges.
            mode: "allow" (default deny) or "deny" (default allow).
            on_blocked: Custom callback when a request is blocked.
                         Receives (req, res, client_ip).
                         Default: returns 403 JSON.
        """
        if mode not in ("allow", "deny"):
            raise ValueError("mode must be 'allow' or 'deny'")
        self.allow = allow or []
        self.deny = deny or []
        self.mode = mode
        self.on_blocked = on_blocked
        self._allow_nets: list[tuple[str, int]] = self._parse_cidrs(self.allow)
        self._deny_nets: list[tuple[str, int]] = self._parse_cidrs(self.deny)

    def _parse_cidrs(self, ips: List[str]) -> list[tuple[str, int]]:
        """Parse IP addresses and CIDR ranges into (ip_str, prefix_len) tuples."""
        import ipaddress
        result = []
        for ip_str in ips:
            ip_str = ip_str.strip()
            if not ip_str:
                continue
            if "/" in ip_str:
                try:
                    net = ipaddress.ip_network(ip_str, strict=False)
                    result.append((str(net.network_address), net.prefixlen))
                except ValueError:
                    # Treat as single IP
                    try:
                        addr = ipaddress.ip_address(ip_str)
                        result.append((str(addr), 32 if addr.version == 4 else 128))
                    except ValueError:
                        pass
            else:
                try:
                    addr = ipaddress.ip_address(ip_str)
                    result.append((str(addr), 32 if addr.version == 4 else 128))
                except ValueError:
                    pass
        return result

    def _ip_in_cidr(self, ip_str: str, nets: list[tuple[str, int]]) -> bool:
        """Check if an IP matches any of the (network, prefix_len) tuples."""
        import ipaddress
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        for network_addr, prefix_len in nets:
            try:
                net = ipaddress.ip_network(f"{network_addr}/{prefix_len}", strict=False)
                if addr in net:
                    return True
            except ValueError:
                pass
        return False

    def _get_client_ip(self, req: Request) -> str:
        """Extract the most reliable client IP from the request."""
        # Check X-Forwarded-For first (for proxied requests)
        xff = req.headers.get("X-Forwarded-For", "")
        if xff:
            # Take the first (original) client IP
            return xff.split(",")[0].strip()

        # Check X-Real-IP
        xri = req.headers.get("X-Real-IP", "")
        if xri:
            return xri.strip()

        # Fall back to direct client IP
        return req.client_ip or "0.0.0.0"

    async def dispatch(
        self,
        req: Request,
        res: Response,
        next_: MiddlewareNext,
    ) -> None:
        client_ip = self._get_client_ip(req)

        # Check deny list first
        if self._ip_in_cidr(client_ip, self._deny_nets):
            self._blocked(req, res, client_ip, "denied (blocklist)")
            return

        if self.mode == "allow":
            if not self._ip_in_cidr(client_ip, self._allow_nets):
                self._blocked(req, res, client_ip, "not in allowlist")
                return

        await next_()

    def _blocked(
        self,
        req: Request,
        res: Response,
        client_ip: str,
        reason: str,
    ) -> None:
        res.headers["X-Blocked-By"] = "IPAllowlistMiddleware"
        if self.on_blocked:
            self.on_blocked(req, res, client_ip)
        else:
            res.status_code = 403
            res.json({
                "error": "Forbidden",
                "message": f"Access denied. {reason}.",
                "ip": client_ip,
            })


# ============================================================
# Observability Middlewares (v5.0.0)
# ============================================================


class MetricsMiddleware(BaseMiddleware):
    """
    Metrics collection middleware for observability.

    Tracks:
    - Request count by (method, path, status_code)
    - Request latency histogram by (method, path)
    - Error count by (method, path)
    - In-progress concurrent requests (gauge)
    - Total bytes sent/received

    Exposes metrics via a ``/metrics`` route (Prometheus-style JSON).

    Example:
        app.use(MetricsMiddleware())

        @app.get("/metrics")
        async def metrics_handler(req, res):
            from pylon.middleware import metrics
            res.json(app._metricsMiddleware.get_metrics())
    """

    METRICS_ROUTE = "/metrics"

    def __init__(self) -> None:
        # Counters: key -> count
        self._counters: dict[str, int] = {}
        # Latencies: key -> list of samples (ms)
        self._latencies: dict[str, list[float]] = {}
        # Errors: key -> count
        self._errors: dict[str, int] = {}
        # In-progress gauge
        self._in_progress: int = 0
        # Total bytes
        self._bytes_sent: int = 0
        self._bytes_received: int = 0
        self._lock = asyncio.Lock()

        # Bucket boundaries for latency histograms (ms)
        self._latency_buckets = [1, 5, 10, 25, 50, 100, 250, 500, 1000, 5000]
        self._latency_histograms: dict[str, dict[str, int]] = {}

    def _make_key(self, method: str, path: str, status_code: int) -> str:
        """Create a metric key from request attributes."""
        # Normalize path to avoid high cardinality from path params
        # Replace numeric path segments with :id
        parts = path.strip("/").split("/")
        normalized = []
        for part in parts:
            if part.isdigit():
                normalized.append(":id")
            elif part and all(c in "0123456789abcdefABCDEF" for c in part) and len(part) == 32:
                # Looks like a hash/UUID, normalize
                normalized.append(":hash")
            else:
                normalized.append(part)
        normalized_path = "/" + "/".join(normalized)
        return f"{method}:{normalized_path}:{status_code}"

    def _make_latency_key(self, method: str, path: str) -> str:
        """Create a latency metric key."""
        parts = path.strip("/").split("/")
        normalized = []
        for part in parts:
            if part.isdigit():
                normalized.append(":id")
            elif part and all(c in "0123456789abcdefABCDEF" for c in part) and len(part) == 32:
                normalized.append(":hash")
            else:
                normalized.append(part)
        return f"{method}:{'/'.join(normalized)}"

    async def dispatch(
        self,
        req: Request,
        res: Response,
        next_: MiddlewareNext,
    ) -> None:
        import time

        start = time.perf_counter()

        async with self._lock:
            self._in_progress += 1
            self._bytes_received += len(req.body)

        error_key: Optional[str] = None

        try:
            await next_()
        except Exception:
            error_key = self._make_key(req.method, req.path, 500)
            async with self._lock:
                self._errors[error_key] = self._errors.get(error_key, 0) + 1
            raise
        finally:
            end = time.perf_counter()
            latency_ms = (end - start) * 1000
            status = res.status_code

            key = self._make_key(req.method, req.path, status)
            lat_key = self._make_latency_key(req.method, req.path)

            async with self._lock:
                self._in_progress -= 1
                self._counters[key] = self._counters.get(key, 0) + 1

                # Latency list (for percentiles)
                if lat_key not in self._latencies:
                    self._latencies[lat_key] = []
                self._latencies[lat_key].append(latency_ms)

                # Histogram buckets
                if lat_key not in self._latency_histograms:
                    self._latency_histograms[lat_key] = {b: 0 for b in self._latency_buckets}
                hist = self._latency_histograms[lat_key]
                for bucket in self._latency_buckets:
                    if latency_ms <= bucket:
                        hist[bucket] += 1

                self._bytes_sent += len(res.body)

    def get_metrics(self) -> dict:
        """
        Return all collected metrics as a dict.

        Includes:
        - counters: request count per (method, path_pattern, status)
        - latency: per-path latency stats (min/max/mean/p50/p95/p99)
        - histograms: latency bucket counts per path
        - errors: error count per (method, path, 500)
        - in_progress: current concurrent request count
        - total_bytes_sent/received
        """
        import statistics

        result: dict[str, Any] = {
            "requests": dict(self._counters),
            "errors": dict(self._errors),
            "in_progress": self._in_progress,
            "total_bytes_sent": self._bytes_sent,
            "total_bytes_received": self._bytes_received,
            "latency": {},
            "histograms": {},
        }

        for key, samples in self._latencies.items():
            if samples:
                sorted_samples = sorted(samples)
                n = len(sorted_samples)
                result["latency"][key] = {
                    "count": n,
                    "min_ms": round(min(samples), 3),
                    "max_ms": round(max(samples), 3),
                    "mean_ms": round(statistics.mean(samples), 3),
                    "p50_ms": round(sorted_samples[int(n * 0.50)], 3),
                    "p95_ms": round(sorted_samples[int(n * 0.95)], 3),
                    "p99_ms": round(sorted_samples[int(n * 0.99)], 3),
                }

        for key, buckets in self._latency_histograms.items():
            result["histograms"][key] = dict(buckets)

        return result

    def reset(self) -> None:
        """Clear all collected metrics."""
        self._counters.clear()
        self._latencies.clear()
        self._errors.clear()
        self._latency_histograms.clear()
        self._in_progress = 0
        self._bytes_sent = 0
        self._bytes_received = 0


class TracingMiddleware(BaseMiddleware):
    """
    Distributed tracing middleware using OpenTelemetry.

    Creates a span for each incoming HTTP request with:
    - Span name: "method path"
    - Span kind: SERVER
    - Trace context propagated via W3C TraceContext headers
    - Request attributes: http.method, http.url, http.route, http.status_code

    Requires OpenTelemetry packages to be installed:
        pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp

    Example:
        app.use(TracingMiddleware(service_name="my-service"))
    """

    def __init__(
        self,
        service_name: str = "pylon",
        exporter: Optional[Any] = None,
        sample_rate: float = 1.0,
    ):
        self.service_name = service_name
        self.exporter = exporter
        self.sample_rate = sample_rate
        self._tracer_provider: Optional[Any] = None
        self._tracer: Optional[Any] = None
        self._setup()

    def _setup(self) -> None:
        """Initialize OpenTelemetry tracer."""
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.semconv.resource import ResourceAttributes
        except ImportError:
            # OpenTelemetry not installed — tracing will be a no-op
            return

        resource = Resource.create({ResourceAttributes.SERVICE_NAME: self.service_name})
        self._tracer_provider = TracerProvider(resource=resource)

        if self.exporter:
            self._tracer_provider.add_span_processor(BatchSpanProcessor(self.exporter))
        else:
            # Default to console exporter in debug mode
            self._tracer_provider.add_span_processor(
                BatchSpanProcessor(ConsoleSpanExporter())
            )

        trace.set_tracer_provider(self._tracer_provider)
        self._tracer = trace.get_tracer(self.service_name)

    async def dispatch(
        self,
        req: Request,
        res: Response,
        next_: MiddlewareNext,
    ) -> None:
        if self._tracer is None:
            # OpenTelemetry not available, proceed without tracing
            await next_()
            return

        span_name = f"{req.method} {req.path}"

        try:
            from opentelemetry import trace
            from opentelemetry.trace import SpanKind, Status, StatusCode
        except ImportError:
            await next_()
            return

        with self._tracer.start_as_current_span(
            span_name,
            kind=SpanKind.SERVER,
        ) as span:
            span.set_attribute("http.method", req.method)
            span.set_attribute("http.url", req.path)
            span.set_attribute("http.route", req.path)
            if req.client_ip:
                span.set_attribute("http.client_ip", req.client_ip)

            try:
                await next_()
                span.set_attribute("http.status_code", res.status_code)
                if res.status_code >= 400:
                    span.set_status(Status(StatusCode.ERROR))
            except Exception as exc:
                span.set_attribute("http.status_code", 500)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                raise


class HealthCheckMiddleware(BaseMiddleware):
    """
    Health check middleware for Kubernetes liveness/readiness probes.

    Registers two endpoints:
    - GET /health/live — liveness probe (always returns 200 if server is running)
    - GET /health/ready — readiness probe (returns 200 when app is ready to serve traffic)

    Override ``is_ready()`` in a subclass for custom readiness logic
    (e.g., database connectivity, cache availability).

    Example:
        app.use(HealthCheckMiddleware())

        # Custom readiness check
        class MyHealthCheck(HealthCheckMiddleware):
            def __init__(self):
                super().__init__()
                self._db_ready = False

            async def is_ready(self) -> bool:
                return self._db_ready

        app.use(MyHealthCheck())
    """

    LIVENESS_ROUTE = "/health/live"
    READINESS_ROUTE = "/health/ready"

    def __init__(self) -> None:
        self._start_time = time.monotonic()

    async def is_ready(self) -> bool:
        """
        Override in subclass for custom readiness logic.

        Default: always return True.
        """
        return True

    async def dispatch(
        self,
        req: Request,
        res: Response,
        next_: MiddlewareNext,
    ) -> None:
        if req.path == self.LIVENESS_ROUTE:
            res.status_code = 200
            res.json({"status": "alive", "uptime_seconds": self._uptime()})
            return

        if req.path == self.READINESS_ROUTE:
            ready = await self.is_ready()
            if ready:
                res.status_code = 200
                res.json({"status": "ready", "uptime_seconds": self._uptime()})
            else:
                res.status_code = 503
                res.json({"status": "not_ready", "uptime_seconds": self._uptime()})
            return

        await next_()

    def _uptime(self) -> float:
        try:
            return time.monotonic() - self._start_time
        except RuntimeError:
            return 0.0


class StaticMiddleware(BaseMiddleware):
    """
    Static file serving middleware.

    Serves files from a local directory at a given URL prefix.
    Supports common file types with appropriate Content-Type headers,
    range requests for large files, and ETag caching.

    Example:
        app.use(StaticMiddleware("/static", "./public"))

        # Serve from multiple roots
        app.use(StaticMiddleware("/assets", "./assets", max_age=86400))
    """

    # Map of file extensions to MIME types
    MIME_TYPES = {
        ".html": "text/html; charset=utf-8",
        ".htm": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".json": "application/json",
        ".xml": "application/xml",
        ".txt": "text/plain; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
        ".webp": "image/webp",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".eot": "application/vnd.ms-fontobject",
        ".pdf": "application/pdf",
        ".zip": "application/zip",
        ".tar": "application/x-tar",
        ".gz": "application/gzip",
        ".map": "application/json",
    }

    def __init__(
        self,
        url_prefix: str = "/static",
        directory: str = "./static",
        max_age: int = 86400,
        index_file: str = "index.html",
    ):
        self.url_prefix = url_prefix.rstrip("/")
        self.directory = directory.rstrip("/")
        self.max_age = max_age
        self.index_file = index_file
        # Cache for file metadata (path -> (mtime, size))
        self._file_cache: dict[str, tuple[float, int]] = {}
        self._cache_lock = asyncio.Lock()

    def _get_mime_type(self, path: str) -> str:
        """Determine MIME type from file extension."""
        import os
        _, ext = os.path.splitext(path)
        ext = ext.lower()
        return self.MIME_TYPES.get(ext, "application/octet-stream")

    def _resolve_path(self, url_path: str) -> Optional[str]:
        """Resolve a URL path to a local file path, or None if not found."""
        import os

        if not url_path.startswith(self.url_prefix + "/"):
            return None

        # Get the relative path under the URL prefix
        rel_path = url_path[len(self.url_prefix):].lstrip("/")
        # Prevent directory traversal
        rel_path = rel_path.replace("..", "")

        full_path = os.path.join(self.directory, rel_path)

        # Security: ensure the resolved path is under self.directory
        real_dir = os.path.realpath(self.directory)
        real_path = os.path.realpath(full_path)
        if not real_path.startswith(real_dir + os.sep) and real_path != real_dir:
            return None

        if os.path.isdir(full_path):
            index = os.path.join(full_path, self.index_file)
            if os.path.isfile(index):
                return index
            return None

        if os.path.isfile(full_path):
            return full_path

        return None

    async def dispatch(
        self,
        req: Request,
        res: Response,
        next_: MiddlewareNext,
    ) -> None:
        import os

        if not req.path.startswith(self.url_prefix + "/") and req.path != self.url_prefix:
            await next_()
            return

        file_path = self._resolve_path(req.path)
        if file_path is None:
            await next_()
            return

        # Check file exists and read it
        if not os.path.isfile(file_path):
            await next_()
            return

        try:
            mtime = os.path.getmtime(file_path)
            size = os.path.getsize(file_path)

            # ETag support
            import hashlib
            etag = hashlib.md5(f"{file_path}:{mtime}".encode()).hexdigest()
            if_none_match = req.headers.get("If-None-Match")
            if if_none_match == etag:
                res.status_code = 304
                res.set_header("ETag", etag)
                res.set_header("Cache-Control", f"public, max-age={self.max_age}")
                return

            # Read file
            with open(file_path, "rb") as f:
                file_data = f.read()

            mime = self._get_mime_type(file_path)
            res.status_code = 200
            res.set_header("Content-Type", mime)
            res.set_header("Content-Length", str(len(file_data)))
            res.set_header("Cache-Control", f"public, max-age={self.max_age}")
            res.set_header("ETag", etag)
            res.set_header("Last-Modified", self._format_http_date(mtime))
            res.body = file_data

        except (OSError, IOError):
            await next_()

    def _format_http_date(self, mtime: float) -> str:
        """Format a modification time as HTTP-date (RFC 7231)."""
        import email.utils
        import time
        return email.utils.formatdate(mtime, usegmt=True)
