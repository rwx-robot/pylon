"""pylon.binding - pydantic-based request binding and validation.

Inspired by Hertz's BindAndValidate, implemented in pure Python.

Example:
    from pydantic import BaseModel
    from pylon import Request, Response
    from pylon.binding import bind, parse_query

    class CreateUser(BaseModel):
        name: str
        email: str
        age: int = 0

    @app.post("/users")
    async def create_user(req: Request, res: Response):
        # Bind and validate JSON body to pydantic model
        user = await bind(req, CreateUser)
        res.json({"created": True, "user": user.model_dump()})

    @app.get("/search")
    async def search(req: Request, res: Response):
        # Parse query params into pydantic model
        params = parse_query(req, SearchParams)
        results = db.search(keyword=params.q, limit=params.limit)
        res.json({"results": results})
"""

from __future__ import annotations

from typing import Any, Dict, Type, TypeVar, Union, get_origin, get_args
import json

from pydantic import BaseModel, ValidationError as PydanticValidationError

from pylon.exceptions import BindingError, ValidationError


T = TypeVar("T", bound=BaseModel)


async def bind(req: Any, model: Type[T]) -> T:
    """
    Bind and validate the request body as a pydantic model.

    Parses the request body as JSON and validates it against the given
    pydantic model. Raises BindingError for parse errors and ValidationError
    for validation failures.

    Args:
        req: The pylon Request object.
        model: A pydantic BaseModel subclass.

    Returns:
        An instance of the model with validated data.

    Raises:
        BindingError: If the body cannot be parsed as JSON.
        ValidationError: If the parsed data fails validation.

    Example:
        class UserCreate(BaseModel):
            name: str
            email: str

        @app.post("/users")
        async def create_user(req, res):
            user = await bind(req, UserCreate)
            res.json({"user": user.model_dump()})
    """
    if not req.body and req.json_data is None:
        raise BindingError("Request body is empty")

    # Use pre-parsed data if BindingMiddleware has run
    if req.json_data is not None:
        data = req.json_data
    else:
        try:
            data = json.loads(req.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BindingError(f"Failed to parse JSON body: {exc}") from exc

    try:
        return model.model_validate(data)
    except PydanticValidationError as exc:
        raise ValidationError(exc.errors()) from exc


async def bind_query(req: Any, model: Type[T]) -> T:
    """
    Bind and validate query parameters as a pydantic model.

    Converts query parameters to the types defined in the pydantic model.
    Only fields defined in the model are extracted from query_params.
    Undefined query params are silently ignored.

    Args:
        req: The pylon Request object.
        model: A pydantic BaseModel subclass.

    Returns:
        An instance of the model with validated query data.

    Example:
        class Pagination(BaseModel):
            page: int = 1
            limit: int = 20

        @app.get("/items")
        async def list_items(req, res):
            params = await bind_query(req, Pagination)
            items = db.fetch(offset=(params.page-1)*params.limit, limit=params.limit)
            res.json({"items": items})
    """
    # Build data dict from query params, applying type coercion via model
    data: Dict[str, Any] = {}
    for field_name, field_info in model.model_fields.items():
        if field_name in req.query_params:
            raw_value = req.query_params[field_name]
            data[field_name] = _coerce_value(raw_value, field_info.annotation)

    try:
        return model.model_validate(data)
    except PydanticValidationError as exc:
        raise ValidationError(exc.errors()) from exc


async def bind_path(req: Any, model: Type[T]) -> T:
    """
    Bind and validate path parameters as a pydantic model.

    Converts path parameters (from route wildcards/params like :id) to the
    types defined in the pydantic model.

    Args:
        req: The pylon Request object.
        model: A pydantic BaseModel subclass.

    Returns:
        An instance of the model with validated path data.

    Example:
        class PathParams(BaseModel):
            user_id: int
            action: str

        @app.get("/users/:user_id/:action")
        async def user_action(req, res):
            params = await bind_path(req, PathParams)
            res.json({"user_id": params.user_id, "action": params.action})
    """
    data: Dict[str, Any] = {}
    for field_name, field_info in model.model_fields.items():
        if field_name in req.path_params:
            raw_value = req.path_params[field_name]
            data[field_name] = _coerce_value(raw_value, field_info.annotation)

    try:
        return model.model_validate(data)
    except PydanticValidationError as exc:
        raise ValidationError(exc.errors()) from exc


def _coerce_value(value: str, annotation: Any) -> Any:
    """
    Coerce a raw string query/path value to the target Python type.

    Handles common types: int, float, bool, str.
    For complex types (List[int], etc.), attempts basic parsing.
    Returns the original string if no coercion is possible.
    """
    origin = get_origin(annotation)

    # Handle Optional
    if origin is Union:
        args = get_args(annotation)
        # Strip NoneType from Union
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _coerce_value(value, non_none[0])

    # Handle List[T] or bare list
    if origin is list or annotation is list:
        args = get_args(annotation)
        item_type = args[0] if args else str
        separator = ","
        items = [item.strip() for item in value.split(separator) if item.strip()]
        return [_coerce_value(item, item_type) for item in items]

    # Scalar types
    if annotation is int or annotation is float:
        try:
            return annotation(value)
        except (ValueError, TypeError):
            return value
    if annotation is bool:
        return value.lower() in ("true", "1", "yes", "on")
    return value


# Backward compatibility alias
parse_query = bind_query
parse_path = bind_path
