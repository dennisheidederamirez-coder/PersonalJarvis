"""Train / validation / untouched test, in time order, with embargo gaps.

- **train:** parameters are fitted here;
- **validation:** candidates are compared and chosen here;
- **test:** opened ONCE, for the single chosen configuration. A second
  opening raises, so the test cannot be used to pick among variants.

An embargo of bars between the parts keeps indicator history and open
positions from leaking across a boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class TestAlreadyUsed(RuntimeError):
    """The untouched test period was already used for a decision."""


@dataclass
class Splits:
    train: slice
    validation: slice
    test: slice
    embargo: int
    _test_opened_for: list[str] = field(default_factory=list)

    def open_test(self, what: str) -> slice:
        if self._test_opened_for:
            raise TestAlreadyUsed(f"the test was already used for {self._test_opened_for[0]!r}")
        self._test_opened_for.append(what)
        return self.test


def three_way(n: int, *, train: float = 0.6, validation: float = 0.2, embargo: int = 0) -> Splits:
    if not (0 < train < 1 and 0 < validation < 1 and train + validation < 1):
        raise ValueError("train and validation shares must leave room for a test")
    a = int(n * train)
    b = int(n * (train + validation))
    if a - embargo <= 0 or b - (a + embargo) <= 0 or n - (b + embargo) <= 0:
        raise ValueError("not enough data for three parts with this embargo")
    return Splits(slice(0, a - embargo), slice(a, b - embargo), slice(b, n), embargo)


__all__ = ["Splits", "TestAlreadyUsed", "three_way"]
