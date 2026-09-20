"""Seven-second delayed progress, including operations without a known total."""
from dataclasses import dataclass, field
from time import monotonic
from threading import Lock


class LatestProgress:
    """Bound worker updates to one pending value instead of flooding GTK's queue."""
    def __init__(self):
        self._lock = Lock()
        self._value = None

    def put(self, text, current=None, total=None):
        with self._lock:
            self._value = (text, current, total)

    def take(self):
        with self._lock:
            value, self._value = self._value, None
            return value


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
