from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class SchemaValidationError(ValueError):
    message: str
    details: dict[str, Any]

    def __str__(self) -> str:
        return self.message


def validate_tool_arguments(
    arguments: dict[str, Any],
    schema: dict[str, Any],
    *,
    defer_conditional: bool = False,
) -> None:
    """Validate the supported JSON Schema subset used by the MCP tool registry.

    Required-field handling intentionally remains in each tool so established
    domain-specific errors such as `credential_missing` and `missing_fields`
    stay stable. This validator rejects unexpected keys and obvious type/enum
    mismatches before any upstream adapter can run.
    """

    defer_schema_conditions = defer_conditional and schema.get("x-weex-deferConditionalValidation") is True
    if schema.get("oneOf") and _one_of_is_decidable(arguments, schema["oneOf"]) and not defer_schema_conditions:
        _validate_one_of(key="$", value=arguments, options=schema["oneOf"])
    if schema.get("allOf") and not defer_schema_conditions:
        for option in schema["allOf"]:
            _validate_value(key="$", value=arguments, property_schema=option)

    allowed = set((schema.get("properties") or {}).keys())
    if schema.get("additionalProperties") is False:
        unknown = sorted(key for key in arguments if key not in allowed)
        if unknown:
            raise SchemaValidationError(
                "Tool arguments contain unsupported fields.",
                {"fields": unknown},
            )

    properties = schema.get("properties") or {}
    for key, value in arguments.items():
        if key not in properties:
            continue
        _validate_value(key=key, value=value, property_schema=properties[key])


def _validate_value(*, key: str, value: Any, property_schema: dict[str, Any]) -> None:
    all_of = property_schema.get("allOf")
    if all_of:
        for option in all_of:
            _validate_value(key=key, value=value, property_schema=option)

    any_of = property_schema.get("anyOf")
    if any_of:
        matches = 0
        for option in any_of:
            try:
                _validate_value(key=key, value=value, property_schema=option)
            except SchemaValidationError:
                continue
            matches += 1
        if matches == 0:
            raise SchemaValidationError("Tool argument does not match any schema variant.", {"field": key, "matching_variants": 0})

    if property_schema.get("oneOf"):
        _validate_one_of(key=key, value=value, options=property_schema["oneOf"])

    if "not" in property_schema:
        try:
            _validate_value(key=key, value=value, property_schema=property_schema["not"])
        except SchemaValidationError:
            pass
        else:
            raise SchemaValidationError("Tool argument matches a forbidden schema.", {"field": key})

    expected_type = property_schema.get("type")
    if expected_type == "string" and not isinstance(value, str):
        raise SchemaValidationError("Tool argument has invalid type.", {"field": key, "expected": "string"})
    if expected_type == "object" and not isinstance(value, dict):
        raise SchemaValidationError("Tool argument has invalid type.", {"field": key, "expected": "object"})
    if expected_type == "array" and not isinstance(value, list):
        raise SchemaValidationError("Tool argument has invalid type.", {"field": key, "expected": "array"})
    if expected_type == "boolean" and not isinstance(value, bool):
        raise SchemaValidationError("Tool argument has invalid type.", {"field": key, "expected": "boolean"})

    const = property_schema.get("const")
    if const is not None and value != const:
        raise SchemaValidationError("Tool argument is not an allowed value.", {"field": key, "allowed": [const]})

    if expected_type == "string":
        min_length = property_schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            raise SchemaValidationError("Tool argument is too short.", {"field": key, "minLength": min_length})
        max_length = property_schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            raise SchemaValidationError("Tool argument is too long.", {"field": key, "maxLength": max_length})
        pattern = property_schema.get("pattern")
        if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
            raise SchemaValidationError("Tool argument has invalid format.", {"field": key, "pattern": pattern})
        integer_range = property_schema.get("x-weex-stringIntegerRange")
        if isinstance(integer_range, dict):
            try:
                parsed = int(value)
            except ValueError:
                raise SchemaValidationError("Tool argument must be an integer string.", {"field": key}) from None
            minimum = integer_range.get("minimum")
            maximum = integer_range.get("maximum")
            if isinstance(minimum, int) and parsed < minimum:
                raise SchemaValidationError("Tool argument is below the allowed range.", {"field": key, "minimum": minimum})
            if isinstance(maximum, int) and parsed > maximum:
                raise SchemaValidationError("Tool argument is above the allowed range.", {"field": key, "maximum": maximum})
        if property_schema.get("x-weex-positiveDecimal"):
            try:
                parsed_decimal = Decimal(value.strip())
            except (InvalidOperation, ValueError):
                raise SchemaValidationError("Tool argument must be a decimal string.", {"field": key}) from None
            if not parsed_decimal.is_finite() or parsed_decimal <= 0:
                raise SchemaValidationError("Tool argument must be a positive decimal string.", {"field": key})
        if property_schema.get("x-weex-nonnegativeDecimal"):
            try:
                parsed_decimal = Decimal(value.strip())
            except (InvalidOperation, ValueError):
                raise SchemaValidationError("Tool argument must be a decimal string.", {"field": key}) from None
            if not parsed_decimal.is_finite() or parsed_decimal < 0:
                raise SchemaValidationError("Tool argument must be a non-negative decimal string.", {"field": key})

    enum = property_schema.get("enum")
    if enum is not None and value not in enum:
        raise SchemaValidationError("Tool argument is not an allowed value.", {"field": key, "allowed": enum})

    if expected_type == "array" or isinstance(value, list):
        min_items = property_schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            raise SchemaValidationError("Tool array argument has too few items.", {"field": key, "minItems": min_items})
        max_items = property_schema.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            raise SchemaValidationError("Tool array argument has too many items.", {"field": key, "maxItems": max_items})
        if property_schema.get("uniqueItems") is True:
            if any(value[index] in value[:index] for index in range(len(value))):
                raise SchemaValidationError("Tool array argument must contain unique items.", {"field": key})
        item_schema = property_schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_value(key=f"{key}[{index}]", value=item, property_schema=item_schema)

    if expected_type == "object" or isinstance(value, dict):
        properties = property_schema.get("properties") or {}
        required = property_schema.get("required") or []
        missing = [field for field in required if field not in value]
        if missing:
            raise SchemaValidationError("Tool object argument is missing required fields.", {"field": key, "fields": missing})
        if property_schema.get("additionalProperties") is False:
            unknown = sorted(field for field in value if field not in properties)
            if unknown:
                raise SchemaValidationError("Tool object argument contains unsupported fields.", {"field": key, "fields": unknown})
        for nested_key, nested_value in value.items():
            nested_schema = properties.get(nested_key)
            if nested_schema is not None:
                _validate_value(key=f"{key}.{nested_key}", value=nested_value, property_schema=nested_schema)
        maximum_time_range_ms = property_schema.get("x-weex-maxTimeRangeMs")
        if isinstance(maximum_time_range_ms, int):
            start_raw = value.get("start_time")
            end_raw = value.get("end_time")
            if start_raw not in (None, "") and end_raw not in (None, ""):
                try:
                    start = int(start_raw)
                    end = int(end_raw)
                except (TypeError, ValueError):
                    raise SchemaValidationError(
                        "Tool time range must use integer strings.",
                        {"field": key},
                    ) from None
                if end < start or end - start > maximum_time_range_ms:
                    raise SchemaValidationError(
                        "Tool time range exceeds the endpoint limit.",
                        {"field": key, "maximumRangeMs": maximum_time_range_ms},
                    )


def _validate_one_of(*, key: str, value: Any, options: list[dict[str, Any]]) -> None:
    matches = 0
    for option in options:
        try:
            _validate_value(key=key, value=value, property_schema=option)
        except SchemaValidationError:
            continue
        matches += 1
    if matches != 1:
        raise SchemaValidationError(
            "Tool argument must match exactly one schema variant.",
            {"field": key, "matching_variants": matches},
        )


def _one_of_is_decidable(value: dict[str, Any], options: list[dict[str, Any]]) -> bool:
    """Defer conditional domain errors until discriminator fields are present.

    Tool handlers intentionally return stable ``missing_fields`` errors.  A
    top-level oneOf should reject an invalid combination once its market/mode
    discriminators are supplied, but must not turn a partially formed request
    into a generic schema error before the handler can report missing fields.
    Nested oneOf values (such as credential variants) are always validated by
    ``_validate_value``.
    """
    option_discriminators: list[set[str]] = []
    for option in options:
        current: set[str] = set()
        for key, property_schema in (option.get("properties") or {}).items():
            if "const" in property_schema or property_schema.get("enum"):
                current.add(key)
        option_discriminators.append(current)
    discriminators = set.intersection(*option_discriminators) if option_discriminators else set()
    return not discriminators or discriminators.issubset(value)
