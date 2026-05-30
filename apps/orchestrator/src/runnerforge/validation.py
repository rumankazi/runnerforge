import logging
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


def _log_validation_errors(e: ValidationError, logger: logging.Logger) -> None:
    for err in e.errors():
        field = ".".join(str(x) for x in err["loc"])  # e.g. "permissions.actions"

        logger.error(f"    - {field}: {err['msg']} (got: {err.get('input')!r})")


def parse_github_response(
    model_cls: type[T], data: dict[str, Any], cause_hint: str, logger: logging.Logger
) -> T:
    # Abstraction layer for the model validation and error handling
    try:
        return model_cls.model_validate(data)
    except ValidationError as e:
        _log_validation_errors(e, logger)
        raise RuntimeError(f"{cause_hint} Pydantic detail: {e}") from e
