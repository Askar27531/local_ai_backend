from __future__ import annotations

import json
from typing import Any

import yaml

from skill_app.domain.errors import ToolExecutionError
from skill_app.schemas.tool import ToolContext, ToolResult
from skill_app.tools.inputs import ValidateJsonInput, ValidateJsonSchemaInput, ValidateYamlInput


def validate_json(_: ToolContext, args: ValidateJsonInput) -> ToolResult:
    try:
        parsed = json.loads(args.content)
    except json.JSONDecodeError as exc:
        return ToolResult(
            ok=False,
            output={"valid": False, "line": exc.lineno, "column": exc.colno, "message": exc.msg},
            error_type="json_decode_error",
        )
    return ToolResult(ok=True, output={"valid": True, "value": parsed})


def validate_yaml(_: ToolContext, args: ValidateYamlInput) -> ToolResult:
    try:
        parsed = yaml.safe_load(args.content)
    except yaml.YAMLError as exc:
        return ToolResult(
            ok=False,
            output={"valid": False, "message": str(exc)},
            error_type="yaml_decode_error",
        )
    return ToolResult(ok=True, output={"valid": True, "value": parsed})


def validate_json_schema(_: ToolContext, args: ValidateJsonSchemaInput) -> ToolResult:
    errors: list[str] = []
    _validate_schema_value(args.instance, args.schema_definition, "$", errors)
    return ToolResult(
        ok=not errors,
        output={"valid": not errors, "errors": errors},
        error_type=None if not errors else "json_schema_validation_error",
    )


def _validate_schema_value(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        errors.append(f"{path}: expected {expected_type}, got {type(value).__name__}")
        return

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not in enum")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: value is above maximum {schema['maximum']}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key}: required property is missing")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ToolExecutionError("Schema properties must be an object.")
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, dict):
                _validate_schema_value(value[key], child_schema, f"{path}.{key}", errors)

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate_schema_value(item, schema["items"], f"{path}[{index}]", errors)


def _matches_type(value: Any, expected: str | list[str]) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(value, item) for item in expected)
    type_map = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    matcher = type_map.get(expected)
    if matcher is None:
        raise ToolExecutionError("Unsupported JSON Schema type.", {"type": expected})
    return matcher(value)
