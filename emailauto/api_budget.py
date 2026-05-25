from __future__ import annotations


class ApiCallBudgetExceeded(RuntimeError):
    """Raised before an external API call would exceed the run budget."""


class ApiCallBudget:
    def __init__(self, max_calls: int):
        self.max_calls = max(0, max_calls)
        self.used_calls = 0

    @property
    def remaining(self) -> int:
        return max(self.max_calls - self.used_calls, 0)

    def can_spend(self, calls: int = 1) -> bool:
        return self.remaining >= calls

    def consume(self, label: str, calls: int = 1) -> None:
        if not self.can_spend(calls):
            raise ApiCallBudgetExceeded(
                f"API call budget exhausted before {label}: "
                f"{self.used_calls}/{self.max_calls} calls already used."
            )
        self.used_calls += calls
