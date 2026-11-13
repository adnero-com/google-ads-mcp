"""Result type for the pure core: failures are values, never exceptions."""

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar, Union

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E")


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T

    @property
    def ok(self) -> bool:
        return True


@dataclass(frozen=True)
class Err(Generic[E]):
    error: E

    @property
    def ok(self) -> bool:
        return False


Result = Union[Ok[T], Err[E]]


def map_ok(result: "Result[T, E]", fn: Callable[[T], U]) -> "Result[U, E]":
    return Ok(fn(result.value)) if isinstance(result, Ok) else result


def and_then(
    result: "Result[T, E]", fn: Callable[[T], "Result[U, E]"]
) -> "Result[U, E]":
    return fn(result.value) if isinstance(result, Ok) else result
