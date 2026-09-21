"""pylon.tools.openapi_parser - OpenAPI 3.x spec parser (pure Python, no external deps)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Parameter:
    """An OpenAPI parameter."""
    name: str
    location: str  # "query", "path", "header", "cookie"
    required: bool = False
    schema: Optional[Dict[str, Any]] = None
    description: str = ""


@dataclass
class RequestBody:
    """An OpenAPI request body."""
    required: bool = False
    description: str = ""
    content_type: str = "application/json"
    schema: Optional[Dict[str, Any]] = None


@dataclass
class ResponseContent:
    """An OpenAPI response content."""
    status_code: int
    description: str = ""
    schema: Optional[Dict[str, Any]] = None


@dataclass
class Endpoint:
    """One operation at a path."""
    path: str
    method: str
    operation_id: str = ""
    summary: str = ""
    description: str = ""
    parameters: List[Parameter] = field(default_factory=list)
    request_body: Optional[RequestBody] = None
    responses: List[ResponseContent] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    deprecated: bool = False


@dataclass
class OpenAPISpec:
    """Parsed OpenAPI specification."""
    title: str = ""
    version: str = ""
    description: str = ""
    endpoints: List[Endpoint] = field(default_factory=list)
    schemas: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def _parse_schema(raw: Any) -> Dict[str, Any]:
    """Return schema dict, resolving $ref if needed."""
    if isinstance(raw, dict):
        if "$ref" in raw:
            return {"type": "ref", "ref": raw["$ref"]}
        return dict(raw)
    return {}


def _load_spec(src: Path | str) -> Dict[str, Any]:
    """Load OpenAPI spec from a YAML or JSON file."""
    path = Path(src)
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        # Minimal YAML parser without external deps
        try:
            import yaml
            return yaml.safe_load(text)
        except ImportError:
            return _yaml_load_without_lib(text)
    else:
        return json.loads(text)


def _yaml_load_without_lib(text: str) -> Dict[str, Any]:
    """Minimal YAML loader for simple OpenAPI specs (no PyYAML dependency).

    Handles the common OpenAPI spec format. For full YAML support, install pyyaml.
    """
    import re

    def parse_value(s: str) -> Any:
        s = s.strip()
        if s == "true":
            return True
        if s == "false":
            return False
        if s == "null":
            return None
        # Try numeric
        try:
            return int(s)
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            pass
        # String literal (strip quotes)
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
            return s[1:-1]
        return s

    lines = text.splitlines()
    result: Dict[str, Any] = {}
    stack: List[tuple] = [(result, None)]
    current_indent = 0

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        content = line.strip()

        # Pop stack to correct level
        while stack and indent <= current_indent:
            stack.pop()
            if stack:
                current_indent = indent

        parent = stack[-0][0] if stack else result

        if content.startswith("{"):
            # Inline dict - ignore for now
            continue

        if ": " in content and not content.startswith("-"):
            key, val = content.split(": ", 1)
            val = parse_value(val) if val else None
            if indent == 0:
                result[key] = val
                stack.append((result, key))
            else:
                parent[key] = val
                stack.append((parent, key))
        elif content.startswith("- "):
            item = content[2:]
            if isinstance(parent, list):
                parent.append(parse_value(item))
            else:
                arr_key = stack[-0][1] if stack else None
                if arr_key and isinstance(result.get(arr_key), list):
                    result[arr_key].append(parse_value(item))

    return result


def parse_openapi_spec(src: Path | str) -> OpenAPISpec:
    """Parse an OpenAPI 3.x spec file and return structured data."""
    raw = _load_spec(src)
    spec = OpenAPISpec(
        title=raw.get("info", {}).get("title", ""),
        version=raw.get("info", {}).get("version", ""),
        description=raw.get("info", {}).get("description", ""),
        schemas=raw.get("components", {}).get("schemas", {}),
        raw=raw,
    )

    paths: Dict[str, Any] = raw.get("paths", {})
    for path, path_item in paths.items():
        for method in ("get", "post", "put", "delete", "patch", "options", "head"):
            if method not in path_item:
                continue
            op = path_item[method]
            endpoint = Endpoint(
                path=path,
                method=method.upper(),
                operation_id=op.get("operationId", ""),
                summary=op.get("summary", ""),
                description=op.get("description", ""),
                deprecated=op.get("deprecated", False),
                tags=op.get("tags", []) or [],
            )

            # Parameters
            for param in op.get("parameters", []):
                endpoint.parameters.append(Parameter(
                    name=param.get("name", ""),
                    location=param.get("in", "query"),
                    required=param.get("required", False),
                    schema=param.get("schema"),
                    description=param.get("description", ""),
                ))

            # Request body
            rb = op.get("requestBody")
            if rb:
                content = rb.get("content", {})
                ct = "application/json"
                c = content.get(ct, content.get("application/yaml", {}))
                endpoint.request_body = RequestBody(
                    required=rb.get("required", False),
                    description=rb.get("description", ""),
                    content_type=ct,
                    schema=c.get("schema"),
                )

            # Responses
            for status_str, resp in op.get("responses", {}).items():
                status_code = int(status_str.replace("x", "0").split("+")[0]) if not status_str.isdigit() else int(status_str)
                content = resp.get("content", {})
                ct = "application/json"
                c = content.get(ct, {})
                spec_dict = c.get("schema")
                endpoint.responses.append(ResponseContent(
                    status_code=status_code,
                    description=resp.get("description", ""),
                    schema=spec_dict,
                ))

            spec.endpoints.append(endpoint)

    return spec
