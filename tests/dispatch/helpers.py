"""Immutable message builders for Dispatch regression scenarios."""

from contracts.enums import CabinMode, Direction, DoorState, StopKind
from contracts.messages import CabinTelemetry, Stop


def telemetry(position=40.0, **changes):
    values = dict(
        t_sim=0.0,
        car_id="P1",
        position=position,
        direction=Direction.IDLE,
        moving=False,
        door=DoorState.CLOSED,
        locked=True,
        door_zone=True,
        obstruction=False,
        load_kg=0.0,
        passengers=0,
        mode=CabinMode.AUTO,
        route_version=0,
    )
    values.update(changes)
    return CabinTelemetry(**values)


def destination(floor, passenger):
    return Stop(floor=floor, kind=StopKind.CAR_DEST, ref_id=passenger)


def pickup(floor, ref="HC-new", direction=Direction.DOWN):
    return Stop(floor=floor, kind=StopKind.HALL_PICKUP, ref_id=ref, direction=direction)
