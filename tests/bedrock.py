"""Bedrock test metadata helpers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

import pytest

TestFunc = TypeVar("TestFunc", bound=Callable[..., object])


@dataclass(frozen=True)
class BedrockCase:
    """Metadata for a critical bedrock contract test."""

    case_id: str
    purpose: str
    use_case: str
    similar_use_cases: tuple[str, ...]
    reliance_percent: float


def bedrock_case(
    case_id: str,
    *,
    purpose: str,
    use_case: str,
    similar_use_cases: Sequence[str],
    reliance_percent: float,
) -> Callable[[TestFunc], TestFunc]:
    """Attach bedrock metadata and mark the test as bedrock-critical."""

    if not 0 < reliance_percent <= 100:
        raise ValueError("reliance_percent must be between 0 and 100.")

    case = BedrockCase(
        case_id=case_id,
        purpose=purpose,
        use_case=use_case,
        similar_use_cases=tuple(similar_use_cases),
        reliance_percent=float(reliance_percent),
    )

    def decorator(func: TestFunc) -> TestFunc:
        setattr(func, "__bedrock_case__", case)
        return pytest.mark.bedrock(func)

    return decorator
