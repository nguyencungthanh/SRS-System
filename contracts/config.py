"""Every SRS constant and team decision as a named value (framework F3).

Code never hard-codes these numbers (CONTRIBUTING.md, Section 8). Values the SRS leaves
open come from DECISIONS.md; the D-xx id is noted next to each one.

R and W are NOT module-level constants: the evaluator runs many (R, W) pairs in one
process, so they travel as a ``Policy`` value (D-17). ``DEFAULT_POLICY`` is the SRS
baseline (3, 5).
"""

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class Policy:
    """A versioned dispatch grouping configuration theta = (R, W) (4.8.1, OPT-06)."""

    version: str
    R: int  # max floor span of a same-direction group (DC-03)
    W: int  # max anchor time window in simulated seconds (DC-03)


# --- Building and fleet (SYS-01) -------------------------------------------------------

FLOORS = 40
MIN_FLOOR = 1
MAX_FLOOR = 40
CARS = ("P1", "P2", "P3", "P4", "G1")
PASSENGER_CARS = ("P1", "P2", "P3", "P4")
GOODS_CARS = ("G1",)
CAR_ORDER = CARS  # final tie-break order (D-13)

# --- Motion and doors (2.4, 7.3, 7.4) ------------------------------------------------

SECONDS_PER_FLOOR = 2.0  # eq. (1)
DWELL_S = 6.0  # per served stop
DOOR_OPENING_S = 1.0  # D-03
DOOR_OPEN_S = 4.0  # D-03
DOOR_CLOSING_S = 1.0  # D-03; OPENING + OPEN + CLOSING == DWELL_S
INSERT_CUTOFF_FLOORS = 1.0  # MOVE-06: need at least this distance to a new stop

# --- Load (CB-02, SEN-03) ------------------------------------------------------------

FULL_LOAD_KG = 900.0  # at or above: bypass new ordinary pickups
MAX_LOAD_KG = 1000.0  # above: no motion
DEFAULT_PASSENGER_KG = 75.0  # D-07

# --- Dispatch (4.1, 4.7) -------------------------------------------------------------

DEFAULT_R = 3
DEFAULT_W = 5
POLICY_VERSION = "POL-0"
DEFAULT_POLICY = Policy(version=POLICY_VERSION, R=DEFAULT_R, W=DEFAULT_W)
DELAY_CAP_S = 10.0  # DC-04, against the FIXED committed reference ETA
AGE_LIMIT_S = 60.0  # DC-06
ACK_TIMEOUT_S = 2.0  # D-10: RouteProposal.expiry_ts = t_sim + ACK_TIMEOUT_S

# --- Telemetry and messaging ---------------------------------------------------------

TELEMETRY_HEARTBEAT_S = 1.0  # D-11: at least this often, plus on every change
TELEMETRY_STALE_S = 3.0  # D-11: older telemetry makes a car ineligible
DEFAULT_DELIVERY_DELAY_S = 0.0  # D-06

# --- Capabilities (D-20, D-21) -------------------------------------------------------

MEDICAL_CAPABLE = frozenset({"P1", "P2", "P3", "P4"})
FREIGHT_CAPABLE = frozenset({"G1", "P1", "P2", "P3", "P4"})
FREIGHT_FIRST_CHOICE = "G1"  # tried first; otherwise a capable P-car
FREIGHT_MAX_KG = MAX_LOAD_KG

# --- Offline optimization (4.8, D-32..D-35) ------------------------------------------

LAMBDA = 0.5  # weight of P95 wait in J(theta)
MU_S = 100.0  # weight of the unserved fraction in J(theta), seconds
R_DOMAIN = (1, 2, 3, 4, 5)
W_DOMAIN = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
TRAIN_SEEDS = (1, 2, 3, 4, 5)
HOLDOUT_SEEDS = (101, 102, 103, 104, 105)
TRACE_DURATION_S = 1800.0  # random Hall Calls are generated in [0, this)
DRAIN_S = 300.0  # D-31: extra time without new calls before counting unserved
SATSA_MAX_EVALS = 30  # D-35: unique (R, W) evaluations per SATSA run
