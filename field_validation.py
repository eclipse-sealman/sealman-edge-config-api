import re
from typing import Any, Dict


def validate_value(field_name: str, value: Any, field_def: Dict[str, Any]) -> None:
    """Validate `value` against a FieldDefinition (as a plain dict) for type, validation, and options.

    Raises ValueError with a human-readable message on the first violation found.
    Shared by the FieldDefinition model (to check `default`) and the data layer
    (to check submitted endpoint_data/service_data), so the rules only live once.
    """
    field_type = field_def.get("type")

    if field_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"'{field_name}' must be a boolean")
        return
    if field_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"'{field_name}' must be an integer")
    elif field_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"'{field_name}' must be a number")
    elif field_type == "string":
        if not isinstance(value, str):
            raise ValueError(f"'{field_name}' must be a string")

    validation: Dict[str, Any] = field_def.get("validation") or {}

    if field_type == "string":
        pattern = validation.get("pattern")
        if pattern is not None and re.fullmatch(pattern, value) is None:
            raise ValueError(f"'{field_name}' does not match the required pattern")
        min_length = validation.get("min_length")
        if min_length is not None and len(value) < min_length:
            raise ValueError(f"'{field_name}' must be at least {min_length} characters long")
        max_length = validation.get("max_length")
        if max_length is not None and len(value) > max_length:
            raise ValueError(f"'{field_name}' must be at most {max_length} characters long")
    elif field_type in ("integer", "number"):
        minimum = validation.get("minimum")
        if minimum is not None:
            if validation.get("exclusive_minimum") and value <= minimum:
                raise ValueError(f"'{field_name}' must be greater than {minimum}")
            if not validation.get("exclusive_minimum") and value < minimum:
                raise ValueError(f"'{field_name}' must be at least {minimum}")
        maximum = validation.get("maximum")
        if maximum is not None:
            if validation.get("exclusive_maximum") and value >= maximum:
                raise ValueError(f"'{field_name}' must be less than {maximum}")
            if not validation.get("exclusive_maximum") and value > maximum:
                raise ValueError(f"'{field_name}' must be at most {maximum}")
        multiple_of = validation.get("multiple_of")
        if multiple_of is not None and value % multiple_of != 0:
            raise ValueError(f"'{field_name}' must be a multiple of {multiple_of}")

    options = field_def.get("options")
    if options is not None and value not in options:
        raise ValueError(f"'{field_name}' must be one of {options}")
