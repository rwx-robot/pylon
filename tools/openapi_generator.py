"""pylon.tools.openapi_generator - Generate pylon route handlers from OpenAPI 3.x specs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Set

from pylon.tools.openapi_parser import OpenAPISpec, Endpoint


# ---------------------------------------------------------------------------
# Name transformation
# ---------------------------------------------------------------------------

def _to_python_type(schema: Dict[str, Any]) -> str:
    """Map OpenAPI schema type to a Python type annotation string."""
    if not schema:
        return "Any"
    typ = schema.get("type", "object")
    if typ == "string":
        fmt = schema.get("format", "")
        if fmt == "date":
            return "date"
        if fmt == "date-time":
            return "datetime"
        return "str"
    if typ == "integer":
        return "int"
    if typ == "number":
        return "float"
    if typ == "boolean":
        return "bool"
    if typ == "array":
        items = schema.get("items", {})
        item_type = _to_python_type(items)
        return f"List[{item_type}]"
    if typ == "object" or "properties" in schema:
        return "Dict[str, Any]"
    return "Any"


def _schema_to_pydantic(name: str, schema: Dict[str, Any], schemas: Dict[str, Any], visited: Set[str]) -> str:
    """Generate a pydantic model class string from a schema."""
    if name in visited:
        return name
    visited.add(name)

    lines: List[str] = []
    lines.append("")
    lines.append(f"class {name}(BaseModel):")

    if schema.get("description"):
        lines.append(f'    """{schema["description"]}"""')

    properties = schema.get("properties", {})
    required = schema.get("required", [])

    if not properties:
        lines.append("    pass")
    else:
        for prop_name, prop_schema in properties.items():
            json_field = prop_name
            required_flag = prop_name in required
            prop_type = _schema_to_pydantic_type(prop_name, prop_schema, schemas, visited)

            # Field annotation
            field_parts = [f"{prop_name}: {prop_type}"]
            if not required_flag:
                field_parts.append("= None")
            field_parts.append(f' = Field(alias="{json_field}")' if json_field != prop_name else " = Field()")
            lines.append("    " + "".join(field_parts))

    return "\n".join(lines)


def _schema_to_pydantic_type(name: str, schema: Dict[str, Any], schemas: Dict[str, Any], visited: Set[str]) -> str:
    """Map an OpenAPI schema to a Python/pydantic type annotation."""
    if not schema:
        return "Any"

    # Resolve $ref
    if "$ref" in schema:
        ref = schema["$ref"]
        model_name = _ref_to_name(ref)
        # Recursively generate if not yet visited
        if model_name not in visited and model_name in schemas:
            _schema_to_pydantic(model_name, schemas[model_name], schemas, visited)
        return model_name

    typ = schema.get("type", "object")

    if typ == "array":
        items = schema.get("items", {})
        item_type = _schema_to_pydantic_type(name, items, schemas, visited)
        return f"List[{item_type}]"

    if typ == "object" or "properties" in schema:
        # Inline object - generate a nested class
        model_name = _pascal_case(name) + "Schema"
        if model_name not in visited:
            _schema_to_pydantic(model_name, schema, schemas, visited)
        return model_name

    if typ == "string":
        fmt = schema.get("format", "")
        if fmt == "date":
            return "date"
        if fmt == "date-time":
            return "datetime"
        return "str"
    if typ == "integer":
        return "int"
    if typ == "number":
        return "float"
    if typ == "boolean":
        return "bool"

    return "Any"


def _ref_to_name(ref: str) -> str:
    """Extract model name from a $ref string like #/components/schemas/User."""
    parts = ref.split("/")
    return parts[-1] if parts else "Unknown"


def _pascal_case(s: str) -> str:
    """Convert a string to PascalCase."""
    s = re.sub(r"[^a-zA-Z0-9]", "_", s)
    s = re.sub(r"_+(.)", r"\1", s.title().replace("_", ""))
    return s or "Schema"


def _to_snake_case(s: str) -> str:
    """Convert a string to snake_case."""
    s = re.sub(r"[^a-zA-Z0-9]", "_", s)
    s = re.sub(r"([A-Z])", r"_\1", s).lower()
    return re.sub(r"_+", "_", s).strip("_")


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------

def _generate_handler(endpoint: Endpoint, schemas: Dict[str, Any]) -> str:
    """Generate a single route handler function from an endpoint."""
    lines: List[str] = []
    op_id = endpoint.operation_id or _to_snake_case(f"{endpoint.method}_{endpoint.path.replace('/', '_')}")
    op_id = op_id.replace("/", "_").replace(":", "_").strip("_")

    doc_parts: List[str] = []
    if endpoint.summary:
        doc_parts.append(endpoint.summary)
    if endpoint.description:
        doc_parts.append(f"\n    {endpoint.description}")
    if endpoint.deprecated:
        doc_parts.append("\n    .. deprecated::")

    # Docstring
    if doc_parts:
        lines.append(f"async def {op_id}(req, res):")
        lines.append(f'    """{"".join(doc_parts)}"""')
    else:
        lines.append(f"async def {op_id}(req, res):")
        lines.append(f'    """Handle {endpoint.method} {endpoint.path}."""')

    # Path params check
    path_params = [p for p in endpoint.parameters if p.location == "path"]
    if path_params:
        lines.append("    # Path parameters")
        for p in path_params:
            ptype = _to_python_type(p.schema or {}) if p.schema else "str"
            ptype_clean = ptype.replace("Dict[str, Any]", "Any")
            lines.append(f"    # {p.name}: {ptype_clean} (path)")

    # Query params
    query_params = [p for p in endpoint.parameters if p.location == "query"]
    if query_params:
        lines.append("    # Query parameters")
        for p in query_params:
            ptype = _to_python_type(p.schema or {}) if p.schema else "str"
            lines.append(f"    # {p.name}: {ptype} (query)")

    # Request body
    body_model = ""
    if endpoint.request_body and endpoint.request_body.schema:
        schema = endpoint.request_body.schema
        if "$ref" in schema:
            body_model = _ref_to_name(schema["$ref"])
        elif schema.get("type") == "array" and "$ref" in schema.get("items", {}):
            items_ref = schema["items"]["$ref"]
            body_model = f"List[{_ref_to_name(items_ref)}]"
        else:
            body_model = "Dict[str, Any]"
        lines.append(f"    # Request body: {body_model}")

    # Response
    if endpoint.responses:
        resp = endpoint.responses[0]
        resp_type = "Dict[str, Any]"
        if resp.schema:
            if "$ref" in resp.schema:
                resp_type = _ref_to_name(resp.schema["$ref"])
            elif resp.schema.get("type") == "array" and "$ref" in resp.schema.get("items", {}):
                resp_type = f"List[{_ref_to_name(resp.schema['items']['$ref'])}]"
        lines.append(f"    # Response: {resp.status_code} → {resp_type}")

    # TODO placeholder
    lines.append(f"    res.status_code = {endpoint.responses[0].status_code if endpoint.responses else 200}")
    if endpoint.responses:
        resp = endpoint.responses[0]
        if resp.schema and "$ref" in resp.schema:
            model_name = _ref_to_name(resp.schema["$ref"])
            lines.append(f"    res.json({{")
            lines.append(f'        "_generated": "{endpoint.method} {endpoint.path}",')
            lines.append(f'        "_model": "{model_name}",')
            lines.append(f'        "_todo": "Replace with actual implementation",')
            lines.append(f"    }})")
        else:
            lines.append(f'    res.json({{"_generated": "{endpoint.method} {endpoint.path}", "_todo": "Replace with actual implementation"}})')
    else:
        lines.append(f'    res.json({{"_generated": "{endpoint.method} {endpoint.path}"}})')

    return "\n".join(lines)


def _generate_routes(spec: OpenAPISpec) -> str:
    """Generate route registration code."""
    lines: List[str] = []
    lines.append("# --- Route Registration ---")
    lines.append("")

    # Group by tag
    by_tag: Dict[str, List[Endpoint]] = {}
    for ep in spec.endpoints:
        tag = (ep.tags[0] if ep.tags else "default").replace(" ", "_").lower()
        by_tag.setdefault(tag, []).append(ep)

    for tag, endpoints in by_tag.items():
        if tag != "default":
            lines.append(f"# [{tag}]")
            lines.append(f"api = app.group('/{tag}')")
            lines.append("")

        for ep in endpoints:
            op_id = ep.operation_id or _to_snake_case(f"{ep.method}_{ep.path.replace('/', '_')}")
            op_id = op_id.replace("/", "_").replace(":", "_").strip("_")

            if tag != "default":
                path = ep.path
                lines.append(f"@api.{ep.method.lower()}('{path}')")
            else:
                lines.append(f"@app.{ep.method.lower()}('{ep.path}')")
            lines.append(f"async def {op_id}(req, res): ...")
            lines.append("")

    return "\n".join(lines)


def _generate_pydantic_models(spec: OpenAPISpec) -> str:
    """Generate all pydantic models from schemas section."""
    lines: List[str] = [
        "from __future__ import annotations",
        "",
        "from datetime import date, datetime",
        "from typing import Any, Dict, List, Optional",
        "",
        "from pydantic import BaseModel, Field",
        "",
        "",
    ]

    visited: Set[str] = set()
    schemas = spec.schemas or {}

    for name, schema in schemas.items():
        if name in visited:
            continue
        if isinstance(schema, dict):
            lines.append(_schema_to_pydantic(name, schema, schemas, visited))

    # Also generate models for request/response schemas referenced in endpoints
    for ep in spec.endpoints:
        if ep.request_body and ep.request_body.schema:
            schema = ep.request_body.schema
            if "$ref" in schema:
                name = _ref_to_name(schema["$ref"])
                if name not in visited and name in schemas:
                    lines.append(_schema_to_pydantic(name, schemas[name], schemas, visited))
            elif schema.get("type") == "array" and "$ref" in schema.get("items", {}):
                name = _ref_to_name(schema["items"]["$ref"])
                if name not in visited and name in schemas:
                    lines.append(_schema_to_pydantic(name, schemas[name], schemas, visited))
        for resp in ep.responses:
            if resp.schema and "$ref" in resp.schema:
                name = _ref_to_name(resp.schema["$ref"])
                if name not in visited and name in schemas:
                    lines.append(_schema_to_pydantic(name, schemas[name], schemas, visited))

    return "\n".join(lines)


def generate_pylon_app(spec: OpenAPISpec, output_dir: Path | str | None = None) -> Dict[str, str]:
    """
    Generate a complete pylon application from an OpenAPI spec.

    Returns a dict mapping filenames to their content:
        - "models.py"     → pydantic models
        - "routes.py"    → route handlers
        - "app.py"       → Application bootstrap

    If output_dir is provided, files are also written there.
    """
    output_dir = Path(output_dir) if output_dir else Path(".")

    # 1. Pydantic models
    models_code = _generate_pydantic_models(spec)

    # 2. Route handlers
    routes_code_lines: List[str] = [
        '"""routes - Generated route handlers from OpenAPI spec."""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
    ]

    # Import models if any
    if spec.schemas:
        routes_code_lines.append("from .models import *")
        routes_code_lines.append("")

    visited_handlers: Set[str] = set()
    for ep in spec.endpoints:
        op_id = ep.operation_id or _to_snake_case(f"{ep.method}_{ep.path.replace('/', '_')}")
        op_id = op_id.replace("/", "_").replace(":", "_").strip("_")
        if op_id in visited_handlers:
            continue
        visited_handlers.add(op_id)
        routes_code_lines.append(_generate_handler(ep, spec.schemas))
        routes_code_lines.append("")

    routes_code = "\n".join(routes_code_lines)

    # 3. App bootstrap
    app_code_lines: List[str] = [
        '"""Generated pylon application."""',
        "",
        "from pylon import Application",
        "from pylon.middleware import RecoveryMiddleware, LoggerMiddleware",
        "",
        f"app = Application(name='{spec.title or 'pylon-app'}')",
        "app.use(RecoveryMiddleware(debug=True))",
        "app.use(LoggerMiddleware())",
        "",
        "# Import generated routes",
        "from .routes import *",
        "",
        "",
        "if __name__ == '__main__':",
        f'    print("Starting {spec.title or "pylon-app"} v{spec.version}")',
        '    app.run(host="0.0.0.0", port=8000)',
    ]
    app_code = "\n".join(app_code_lines)

    files = {
        "models.py": models_code,
        "routes.py": routes_code,
        "app.py": app_code,
    }

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        for filename, content in files.items():
            (output_dir / filename).write_text(content, encoding="utf-8")
        print(f"Generated {len(files)} files in {output_dir}/")

    return files


def generate_openapi_spec(
    title: str,
    version: str,
    description: str = "",
    endpoints: List[Dict[str, Any]] | None = None,
    schemas: Dict[str, Any] | None = None,
    output: Path | str | None = None,
) -> Dict[str, Any]:
    """
    Generate an OpenAPI 3.x spec dict from structured data.

    Args:
        title: API title
        version: API version
        description: API description
        endpoints: List of endpoint specs, each containing:
            - path: str (e.g. "/users/:id")
            - method: str (e.g. "GET")
            - summary: str
            - request_body: dict (OpenAPI request body schema)
            - responses: dict (status_code -> {description, schema})
            - parameters: list of {name, location, required, schema}
        schemas: OpenAPI components/schemas dict
        output: Optional path to write the YAML file

    Returns:
        The OpenAPI spec dict
    """
    spec: Dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": title,
            "version": version,
            "description": description,
        },
        "paths": {},
        "components": {
            "schemas": schemas or {},
        },
    }

    paths: Dict[str, Any] = {}

    for ep in (endpoints or []):
        path = ep.get("path", "/")
        method = ep.get("method", "GET").lower()
        summary = ep.get("summary", "")

        if path not in paths:
            paths[path] = {}

        op: Dict[str, Any] = {
            "summary": summary,
            "operationId": ep.get("operation_id", f"{method}_{path.replace('/', '_').replace(':', '_')}"),
            "tags": ep.get("tags", []),
            "responses": ep.get("responses", {"200": {"description": "OK"}}),
        }

        if "parameters" in ep:
            op["parameters"] = ep["parameters"]

        if "request_body" in ep:
            op["requestBody"] = ep["request_body"]

        paths[path][method] = op

    spec["paths"] = paths

    if output:
        import json
        path = Path(output)
        if path.suffix in (".yaml", ".yml"):
            # Simple YAML output without pyyaml dependency
            content = _dict_to_yaml(spec)
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")

    return spec


def _dict_to_yaml(d: Any, indent: int = 0) -> str:
    """Simple dict-to-YAML converter for OpenAPI specs."""
    lines: List[str] = []
    prefix = "  " * indent

    if isinstance(d, dict):
        for key, val in d.items():
            if isinstance(val, (dict, list)) and val:
                lines.append(f"{prefix}{key}:")
                lines.append(_dict_to_yaml(val, indent + 1))
            elif isinstance(val, str) and (":" in val or "#" in val or val.startswith("{") or val.startswith("[")):
                lines.append(f'{prefix}{key}: "{val}"')
            else:
                lines.append(f"{prefix}{key}: {val}")
    elif isinstance(d, list):
        for item in d:
            if isinstance(item, dict):
                first = True
                for k, v in item.items():
                    if first:
                        lines.append(f"{prefix}- {k}: {repr(v) if isinstance(v, str) else v}")
                        first = False
                    else:
                        lines.append(f"{prefix}  {k}: {repr(v) if isinstance(v, str) else v}")
            else:
                lines.append(f"{prefix}- {repr(item) if isinstance(item, str) else item}")
    else:
        lines.append(f"{prefix}{repr(d) if isinstance(d, str) else d}")

    return "\n".join(lines)
