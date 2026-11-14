"""FakeControlCenter: approves everything well-formed (framework F6).

What it does:
    * MissionRequest: REJECTED (MISSING_FIELD) without origin or destination; otherwise
      APPROVED + MissionApproval with the capable cars from config (D-20, D-21, D-29).
    * Tracks every MissionStatusUpdate for the snapshot; raises an UNAVAILABLE alert when a
      mission first becomes UNAVAILABLE (D-29).
    * Alerts from Alert, AlarmPressed (the only source of ALARM alerts, D-19) and
      FireAlarm(active). alert_id = the message's msg_id. AlertAcknowledged marks it.
    * RecoveryRequest -> RecoveryApproved at once.
    * PolicyRecommended is stored; PolicyDecision(approve) for a known run -> PolicyActivated.

What it skips: role checks, material / payload validation, audit log.
"""

from contracts.config import CAR_ORDER, FREIGHT_CAPABLE, MEDICAL_CAPABLE
from contracts.enums import AlertKind, MissionStatus, MissionType, RejectReason
from contracts.ids import PREFIX_MISSION, IdGen
from contracts.messages import (
    AlarmPressed,
    Alert,
    AlertAcknowledged,
    FireAlarm,
    Message,
    MissionApproval,
    MissionRequest,
    MissionStatusUpdate,
    PolicyActivated,
    PolicyDecision,
    PolicyRecommended,
    RecoveryApproved,
    RecoveryRequest,
)
from contracts.routing import CONTROL_CENTER
from contracts.ui import AlertView, ControlCenterSnapshot, MissionView

CAPABLE = {MissionType.MEDICAL: MEDICAL_CAPABLE, MissionType.FREIGHT: FREIGHT_CAPABLE}


class FakeControlCenter:
    name = CONTROL_CENTER

    def __init__(self) -> None:
        self.missions: dict[str, MissionView] = {}
        self.alerts: dict[str, AlertView] = {}
        self.recommendations: dict[str, PolicyRecommended] = {}
        self._mission_ids = IdGen(PREFIX_MISSION)

    # --- Module interface ------------------------------------------------------------

    def handle(self, msg: Message, now: float) -> list[Message]:
        out: list[Message] = []
        match msg:
            case MissionRequest():
                self._on_request(msg, now, out)
            case MissionStatusUpdate():
                self._on_status(msg, now)
            case Alert():
                self._add_alert(msg.msg_id, msg.kind, msg.car_id, msg.details, now)
            case AlarmPressed():
                self._add_alert(msg.msg_id, AlertKind.ALARM, msg.car_id, "Alarm pressed", now)
            case FireAlarm(active=True):
                self._add_alert(msg.msg_id, AlertKind.FIRE, None, "Fire recall", now)
            case AlertAcknowledged():
                if msg.alert_id in self.alerts:
                    self.alerts[msg.alert_id]["acknowledged"] = True
            case RecoveryRequest():
                out.append(RecoveryApproved(t_sim=now, car_id=msg.car_id, approved_by=msg.role))
            case PolicyRecommended():
                self.recommendations[msg.run_id] = msg
            case PolicyDecision(approve=True) if msg.run_id in self.recommendations:
                out.append(
                    PolicyActivated(t_sim=now, policy=self.recommendations[msg.run_id].policy)
                )
            case _:
                pass
        return out

    def snapshot(self) -> ControlCenterSnapshot:
        return {
            "missions": [dict(v) for v in self.missions.values()],  # type: ignore[misc]
            "alerts": [dict(v) for v in self.alerts.values()],  # type: ignore[misc]
        }

    # --- handlers --------------------------------------------------------------------

    def _on_request(self, msg: MissionRequest, now: float, out: list[Message]) -> None:
        mission_id = self._mission_ids.next()
        view: MissionView = {
            "mission_id": mission_id,
            "mission_type": msg.mission_type.value,
            "origin": msg.origin,
            "destination": msg.destination,
            "status": MissionStatus.APPROVED.value,
            "car_id": None,
            "reason": None,
            "candidates": [],
        }
        self.missions[mission_id] = view
        if msg.origin is None or msg.destination is None:
            view["status"] = MissionStatus.REJECTED.value
            view["reason"] = RejectReason.MISSING_FIELD.value
            out.append(
                MissionStatusUpdate(
                    t_sim=now,
                    mission_id=mission_id,
                    status=MissionStatus.REJECTED,
                    reason=RejectReason.MISSING_FIELD,
                )
            )
            return
        allowed = tuple(car for car in CAR_ORDER if car in CAPABLE[msg.mission_type])
        out.append(
            MissionStatusUpdate(t_sim=now, mission_id=mission_id, status=MissionStatus.APPROVED)
        )
        out.append(
            MissionApproval(
                t_sim=now,
                mission_id=mission_id,
                role=msg.role,
                mission_type=msg.mission_type,
                origin=msg.origin,
                destination=msg.destination,
                allowed_cars=allowed,
                material=msg.material,
                weight_kg=msg.weight_kg,
            )
        )

    def _on_status(self, msg: MissionStatusUpdate, now: float) -> None:
        view = self.missions.get(msg.mission_id)
        if view is None:
            return
        was_unavailable = view["status"] == MissionStatus.UNAVAILABLE
        view["status"] = msg.status.value
        if msg.car_id is not None:
            view["car_id"] = msg.car_id
        if msg.reason is not None:
            view["reason"] = msg.reason.value
        if msg.candidates:
            view["candidates"] = [
                {
                    "car_id": c.car_id,
                    "eta_s": c.eta_s,
                    "excluded": c.excluded.value if c.excluded else None,
                }
                for c in msg.candidates
            ]
        if msg.status == MissionStatus.UNAVAILABLE and not was_unavailable:
            self._add_alert(
                msg.msg_id,
                AlertKind.UNAVAILABLE,
                None,
                f"No eligible car for {msg.mission_id}",
                now,
            )

    def _add_alert(
        self, alert_id: str, kind: AlertKind, car_id: str | None, details: str, now: float
    ) -> None:
        self.alerts[alert_id] = {
            "alert_id": alert_id,
            "kind": kind.value,
            "car_id": car_id,
            "details": details,
            "t_sim": now,
            "acknowledged": False,
        }


def create() -> FakeControlCenter:
    return FakeControlCenter()
