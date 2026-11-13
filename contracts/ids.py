"""Deterministic ID generation (DECISIONS.md D-15).

IDs are a prefix plus a counter, never UUIDs, so a replay produces identical logs.
A plain counter is enough: modules are deterministic, so the order of ``next()`` calls is too.

Who generates what:

=========  ======================  ==========================
Prefix     Owner                   Used for
=========  ======================  ==========================
``MSG``    engine                  ``Message.msg_id`` (also ``alert_id``)
``HC``     Dispatch                Hall Call ``request_id``
``RC``     Dispatch                ``RouteProposal.command_id``
``MS``     Control Center          ``mission_id``
``PAX``    scenarios / UI          ``passenger_id``
``RUN``    optimization            optimization ``run_id``
``POL``    optimization            ``Policy.version``
=========  ======================  ==========================
"""

PREFIX_MESSAGE = "MSG"
PREFIX_HALL_CALL = "HC"
PREFIX_COMMAND = "RC"
PREFIX_MISSION = "MS"
PREFIX_PASSENGER = "PAX"
PREFIX_RUN = "RUN"
PREFIX_POLICY = "POL"


class IdGen:
    """``IdGen("HC").next()`` returns ``"HC-0001"``, then ``"HC-0002"``, ..."""

    def __init__(self, prefix: str, width: int = 4) -> None:
        self.prefix = prefix
        self.width = width
        self._count = 0

    def next(self) -> str:
        self._count += 1
        return f"{self.prefix}-{self._count:0{self.width}d}"

    def reset(self) -> None:
        self._count = 0
