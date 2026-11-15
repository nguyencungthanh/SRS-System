"""One WebSocket client's snapshot feed (HY-05, verification finding F1).

An's ``SimRunner.publish()`` awaits every subscriber in turn. If a subscriber awaited the
socket directly, one client that stops reading (frozen tab, dead Wi-Fi) would block the
simulation loop and every other client. So each client gets a ``ClientFeed``:

- ``offer()`` is what the runner calls. It never waits: it keeps only the newest snapshot
  (an older one that was not sent yet is dropped; the UI only needs the latest state).
- ``run()`` is the client's own sender task. Only this task waits for the slow socket.
"""

import asyncio
from collections.abc import Awaitable, Callable

from contracts.ui import Snapshot

Send = Callable[[Snapshot], Awaitable[None]]


class ClientFeed:
    def __init__(self) -> None:
        self._latest: asyncio.Queue[Snapshot] = asyncio.Queue(maxsize=1)

    def offer(self, snap: Snapshot) -> None:
        """Replace whatever is waiting with ``snap``. Never blocks."""
        if self._latest.full():
            self._latest.get_nowait()
        self._latest.put_nowait(snap)

    def pending(self) -> int:
        return self._latest.qsize()

    async def run(self, send: Send) -> None:
        """Send snapshots until ``send`` fails (the client is gone) or the task is cancelled."""
        while True:
            snap = await self._latest.get()
            await send(snap)
