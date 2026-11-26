#!/usr/bin/env python3
# verdict-bench harness/budget.py
#
# The shared budget. One definition, used by both the runner (which enforces it after the
# fact) and the arms (which carry it during the run), so parity is a property of the type
# rather than a convention someone has to remember.
#
# Every field is a ceiling. What an arm actually consumed is measured separately, by the
# arm, and compared here - never declared by the arm.

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Budget:
    """Identical allowance for every arm."""

    max_steps: int
    max_model_calls: int
    max_wall_seconds: float

    def as_dict(self) -> dict:
        return {
            "max_steps": self.max_steps,
            "max_model_calls": self.max_model_calls,
            "max_wall_seconds": self.max_wall_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Budget":
        """Build from a spec block. Missing fields are an error, not a default - a default
        budget is how two arms silently get different limits."""
        required = ("max_steps", "max_model_calls", "max_wall_seconds")
        missing = [k for k in required if k not in data]
        if missing:
            raise ValueError(
                f"budget is missing {', '.join(missing)}; "
                "no defaults are supplied, because arms must not differ silently"
            )
        return cls(
            max_steps=int(data["max_steps"]),
            max_model_calls=int(data["max_model_calls"]),
            max_wall_seconds=float(data["max_wall_seconds"]),
        )
