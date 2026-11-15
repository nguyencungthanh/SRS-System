"""Refused requests seen by the API (HY-05, SRS 5.3, verification finding F5).

SRS 5.3: refusals are logged with timestamps; the 7.8 table lists an audit record as visible
Control Center feedback. A request refused by the API (wrong role, 403) never reaches the
simulation, so no module can record it. The API records it here, together with requests
the simulation refused (409). They are shown in the Control Center tab via ``GET /audit``
and also written to the server log (logger ``api.audit``).

Malformed bodies (422) are input errors, not refusals, and are not recorded. The Control
Center's own audit list (``ControlCenter.audit_log()``) is not reachable from the API yet:
that needs a change in An's ``SimControl`` (see ai-log/huy.md).
"""

import logging
from collections import deque
from typing import TypedDict

MAX_ENTRIES = 500

log = logging.getLogger("api.audit")


class RefusalView(TypedDict):
    t_sim: float
    command: str
    role: str | None
    status: int
    reason: str | None


class RefusalLog:
    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._entries: deque[RefusalView] = deque(maxlen=max_entries)

    def record(
        self, t_sim: float, command: str, role: str | None, status: int, reason: str | None
    ) -> None:
        entry: RefusalView = {
            "t_sim": t_sim,
            "command": command,
            "role": role,
            "status": status,
            "reason": reason,
        }
        self._entries.append(entry)
        log.warning(
            "refused t=%.1f %s role=%s status=%d reason=%s", t_sim, command, role, status, reason
        )

    def entries(self) -> list[RefusalView]:
        """Oldest first, copies."""
        return [dict(e) for e in self._entries]  # type: ignore[misc]
