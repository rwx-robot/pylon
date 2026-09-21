"""pylon.config - Application configuration dataclass.

Provides centralized configuration for pylon applications,
inspired by Hertz's option/config pattern but implemented in pure Python.

Example:
    from pylon import Application, Config

    # Option 1: keyword arguments
    app = Application(name="my-app", debug=True)
    app.run()

    # Option 2: explicit config
    config = Config(
        name="my-app",
        debug=True,
        host="0.0.0.0",
        port=9000,
    )
    app = Application.from_config(config)
    app.run()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Config:
    """
    Centralized application configuration.

    All fields have sensible defaults. Pass to Application.from_config()
    or use keyword arguments directly on Application().

    Attributes:
        name: Application name (used in logs, server title).
        debug: Enable debug mode (detailed error traces).
        host: Default host to bind when running.
        port: Default port to bind when running.
        reload: Enable auto-reload on file changes (uvicorn).
        workers: Number of worker processes (production).
        log_level: Logging level (debug, info, warning, error).
        middleware: List of global middleware callables to register.
        startup_hooks: List of async callables to run on startup.
        shutdown_hooks: List of async callables to run on shutdown.
        request_id_header: Header name for request ID injection.
        cors_allow_origins: CORS allowed origins list.
        cors_allow_methods: CORS allowed HTTP methods.
        cors_allow_headers: CORS allowed headers.
        json_encoders: Custom JSON encoders dict (type -> encoder callable).
        extra: Arbitrary extra settings dict for user extensions.
    """

    name: str = "pylon"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False
    workers: int = 1
    log_level: str = "info"

    # Middleware
    middleware: List[Callable] = field(default_factory=list)

    # Lifecycle
    startup_hooks: List[Callable] = field(default_factory=list)
    shutdown_hooks: List[Callable] = field(default_factory=list)

    # Request ID
    request_id_header: str = "X-Request-ID"

    # CORS
    cors_allow_origins: List[str] = field(default_factory=list)
    cors_allow_methods: List[str] = field(default_factory=lambda: ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    cors_allow_headers: List[str] = field(default_factory=lambda: ["*"])

    # JSON
    json_encoders: Dict[type, Callable] = field(default_factory=dict)

    # Extensions
    extra: Dict[str, Any] = field(default_factory=dict)

    def update(self, **kwargs: Any) -> Config:
        """
        Return a new Config with updated fields (copy-on-write).

        Does not modify the original instance.
        """
        import copy
        new_config = copy.deepcopy(self)
        for key, value in kwargs.items():
            if hasattr(new_config, key):
                setattr(new_config, key, value)
        return new_config
