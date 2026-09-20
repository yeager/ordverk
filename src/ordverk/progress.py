"""Seven-second delayed progress, including operations without a known total."""
from dataclasses import dataclass, field
from time import monotonic


@dataclass
class ProgressState:
    started: float = field(default_factory=monotonic)
    current: int | None = None
    total: int | None = None
    delay: float = 7.0

    def visible(self, now=None):
        return (monotonic() if now is None else now) - self.started >= self.delay

    @property
    def fraction(self):
        if self.total is None or self.total <= 0 or self.current is None:
            return None
        return min(1.0, max(0.0, self.current / self.total))

    def update(self, current=None, total=None):
        self.current, self.total = current, total
