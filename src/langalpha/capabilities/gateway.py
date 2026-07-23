from __future__ import annotations

import threading
from collections import OrderedDict

from langalpha.agent.context import RunContext


class CapabilityGateway:
    """Small per-run admission gate for first-party domain capabilities."""

    def __init__(self, max_calls_per_run: int = 100) -> None:
        self.max_calls_per_run = max_calls_per_run
        self._lock = threading.Lock()
        self._calls: OrderedDict[tuple[str, str], int] = OrderedDict()

    def admit(self, capability_id: str, context: RunContext) -> None:
        key = (context.product_run_id, capability_id)
        with self._lock:
            count = self._calls.get(key, 0) + 1
            self._calls[key] = count
            self._calls.move_to_end(key)
            while len(self._calls) > 2_000:
                self._calls.popitem(last=False)
        if count > self.max_calls_per_run:
            raise RuntimeError(f"{capability_id} call budget exceeded")


gateway = CapabilityGateway()
