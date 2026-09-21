"""
pylon.cli - Command-line interface for pylon code generation and project scaffolding.

Usage:
    python -m pylon.cli --help
    python -m pylon.cli new myproject --openapi openapi.yaml
    python -m pylon.cli generate openapi openapi.yaml --output ./generated
    python -m pylon.cli route GET /users/:id
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

__all__ = ["cli"]

HELP = """
pylon CLI - Code generation and project scaffolding for pylon.

Commands:
    new               Create a new pylon project
    generate openapi  Generate code from OpenAPI spec
    generate spec     Generate an OpenAPI spec file
    route             Generate a route handler scaffold

Examples:
    pylon new myservice --openapi openapi.yaml
    pylon generate openapi openapi.yaml --output ./generated
    pylon route GET /users/:id --output routes.py
"""


def _import_click():
    """Import click, install hint if not available."""
    try:
        import click
        return click
    except ImportError:
        print(
            "ERROR: 'click' is required for the pylon CLI. "
            "Install it with: pip install click",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Project scaffolding
# ---------------------------------------------------------------------------

def _create_project(
    name: str,
    output_dir: Optional[Path] = None,
    openapi_spec: Optional[str] = None,
) -> None:
    """Scaffold a new pylon project."""
    import shutil

    output_dir = output_dir or Path.cwd() / name
    if output_dir.exists():
        print(f"ERROR: Directory already exists: {output_dir}", file=sys.stderr)
        sys.exit(1)

    output_dir.mkdir(parents=True)
    (output_dir / "app.py").write_text(_PROJECT_APP_TEMPLATE.format(name=name), encoding="utf-8")
    (output_dir / "main.py").write_text(_PROJECT_MAIN_TEMPLATE.format(name=name), encoding="utf-8")
    (output_dir / "requirements.txt").write_text(_REQUIREMENTS_TEMPLATE, encoding="utf-8")
    (output_dir / "pyproject.toml").write_text(_PYPROJECT_TEMPLATE.format(name=name), encoding="utf-8")

    src_dir = output_dir / name
    src_dir.mkdir()
    (src_dir / "__init__.py").write_text('"""My pylon service."""\n', encoding="utf-8")
    (src_dir / "routes.py").write_text('"""Route handlers."""\n', encoding="utf-8")
    (src_dir / "models.py").write_text(
        "from pydantic import BaseModel\n\n\n# Add your models here\n",
        encoding="utf-8",
    )
    (src_dir / "middleware.py").write_text('"""Custom middleware."""\n', encoding="utf-8")

    if openapi_spec and Path(openapi_spec).exists():
        shutil.copy(openapi_spec, output_dir / "openapi.yaml")
        from pylon.tools.openapi_parser import parse_openapi_spec
        from pylon.tools.openapi_generator import generate_pylon_app
        spec = parse_openapi_spec(openapi_spec)
        generate_pylon_app(spec, output_dir=src_dir)
        print("  + Generated routes from OpenAPI spec")

    print(f"Created pylon project: {name}")
    print(f"  -> {output_dir}/")
    print("\nNext steps:")
    print(f"  cd {name}")
    print(f"  pip install -e .")
    print(f"  python main.py")


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------

def _generate_openapi(
    spec_path: str,
    output: str,
    models_only: bool,
    routes_only: bool,
) -> None:
    """Generate pylon code from an OpenAPI spec."""
    from pylon.tools.openapi_parser import parse_openapi_spec
    from pylon.tools.openapi_generator import (
        generate_pylon_app,
        _generate_pydantic_models,
        _generate_handler,
    )

    spec = parse_openapi_spec(spec_path)
    print(f"OpenAPI: {spec.title} v{spec.version}")
    print(f"  Endpoints: {len(spec.endpoints)}")
    print(f"  Schemas: {len(spec.schemas)}")

    if models_only:
        code = _generate_pydantic_models(spec)
        print(code)
        return

    if routes_only:
        for ep in spec.endpoints:
            print(_generate_handler(ep, spec.schemas))
            print()
        return

    out_dir = Path(output)
    files = generate_pylon_app(spec, output_dir=out_dir)
    for filename in sorted(files):
        print(f"  -> {out_dir / filename}")
    print(f"\nGenerated {len(files)} files in {out_dir}/")


def _generate_spec(
    title: str,
    version: str,
    description: str,
    output: str,
) -> None:
    """Generate an OpenAPI spec YAML file."""
    from pylon.tools.openapi_generator import generate_openapi_spec

    spec = generate_openapi_spec(
        title=title,
        version=version,
        description=description,
    )
    import json
    out_path = Path(output)
    if out_path.suffix in (".yaml", ".yml"):
        from pylon.tools.openapi_generator import _dict_to_yaml
        content = _dict_to_yaml(spec)
    else:
        content = json.dumps(spec, indent=2, ensure_ascii=False)
    out_path.write_text(content, encoding="utf-8")
    print(f"OpenAPI spec -> {out_path}")


def _generate_route(
    method: str,
    path: str,
    output: Optional[str],
) -> None:
    """Generate a route handler scaffold."""
    method = method.upper()
    safe_path = path.replace("/", "_").replace(":", "_").strip("_")
    op_id = f"handle_{method.lower()}_{safe_path}"
    code = f'''"""Route handler scaffold: {method} {path}"""

from pylon import Application, Request, Response

app = Application()


@app.{method.lower()}("{path}")
async def {op_id}(req: Request, res: Response) -> None:
    """Handle {method} {path}."""
    # Path parameters: {{k: req.path_params.get(k) for k in req.path_params}}
    # Query parameters: {{k: req.query_params.get(k) for k in req.query_params}}

    res.status_code = 200
    res.json({{
        "message": "TODO: implement {method} {path}",
        "path_params": dict(req.path_params),
        "query_params": dict(req.query_params),
    }})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
'''
    if output:
        Path(output).write_text(code, encoding="utf-8")
        print(f"Route handler -> {output}")
    else:
        print(code)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_PROJECT_APP_TEMPLATE = '''"""Generated pylon application for {name}."""

from pylon import Application
from pylon.middleware import RecoveryMiddleware, LoggerMiddleware

app = Application(name="{name}")
app.use(RecoveryMiddleware(debug=True))
app.use(LoggerMiddleware())

# Import route handlers
from {name} import routes  # noqa: F401
'''

_PROJECT_MAIN_TEMPLATE = '''"""Run the {name} service."""

from {name}.app import app

if __name__ == "__main__":
    print("Starting {name} service on http://0.0.0.0:8000")
    app.run(host="0.0.0.0", port=8000)
'''

_REQUIREMENTS_TEMPLATE = """\
pylon>=3.0.0
uvicorn[standard]>=0.30.0
pydantic>=2.0.0
"""

_PYPROJECT_TEMPLATE = '''\
[project]
name = "{name}"
version = "0.1.0"
description = "pylon service"
requires-python = ">=3.10"
dependencies = [
    "pylon>=3.0.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic>=2.0.0",
]

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"
'''


# ---------------------------------------------------------------------------
# CLI main
# ---------------------------------------------------------------------------

def cli(args: Optional[list[str]] = None) -> int:
    """
    Main CLI entry point.

    Usage:
        python -m pylon.cli new myproject
        python -m pylon.cli generate openapi openapi.yaml
        python -m pylon.cli route GET /users/:id
    """
    click = _import_click()

    @click.group(help=HELP)
    def main():
        """pylon CLI - Code generation for pylon."""
        pass

    # --- new ---
    @main.command("new")
    @click.argument("name")
    @click.option("--output", "-o", help="Output directory", default=None)
    @click.option("--openapi", "-s", "openapi_spec", help="OpenAPI spec file", default=None)
    def cmd_new(name: str, output: Optional[str], openapi_spec: Optional[str]):
        """Create a new pylon project scaffold."""
        _create_project(
            name,
            output_dir=Path(output) if output else None,
            openapi_spec=openapi_spec,
        )

    # --- generate ---
    @main.group("generate")
    def cmd_generate():
        """Generate pylon code from OpenAPI specs."""
        pass

    @cmd_generate.command("openapi")
    @click.argument("spec_path")
    @click.option("--output", "-o", default="generated", help="Output directory")
    @click.option("--models-only", is_flag=True, help="Generate only pydantic models")
    @click.option("--routes-only", is_flag=True, help="Generate only route handlers")
    def cmd_generate_openapi(
        spec_path: str,
        output: str,
        models_only: bool,
        routes_only: bool,
    ):
        """Generate pylon routes and models from an OpenAPI 3.x spec."""
        _generate_openapi(spec_path, output, models_only, routes_only)

    @cmd_generate.command("spec")
    @click.option("--title", "-t", default="My API", help="API title")
    @click.option("--version", "-v", default="0.1.0", help="API version")
    @click.option("--description", "-d", default="", help="API description")
    @click.option("--output", "-o", default="openapi.yaml", help="Output file")
    def cmd_generate_spec(
        title: str,
        version: str,
        description: str,
        output: str,
    ):
        """Generate an empty OpenAPI 3.x spec YAML file."""
        _generate_spec(title, version, description, output)

    # --- route ---
    @main.command("route")
    @click.argument("method")
    @click.argument("path")
    @click.option("--output", "-o", help="Output file", default=None)
    def cmd_route(method: str, path: str, output: Optional[str]):
        """Generate a route handler scaffold.

        Example:
            pylon route GET /users/:id
            pylon route POST /users -o routes.py
        """
        _generate_route(method, path, output)

    args = args or sys.argv[1:]

    if not args:
        click.echo(HELP)
        return 0

    try:
        main(args=args, standalone_mode=True)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(cli())
