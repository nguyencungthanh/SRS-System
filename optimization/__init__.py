"""Offline evaluation and tuning of the grouping policy theta = (R, W) (AN-03..AN-07, SRS 4.8).

    metrics.py    what one simulated run measured; J(theta) (eq. 6)
    evaluator.py  run traces for a policy (parallel, cached), reject unsafe ones (D-66)
    grid.py       every (R, W) in the domain (OPT-05)
    satsa.py      SATSA search (OPT-02, OPT-03), adapted to an integer domain
    report.py     comparison on held-out traces, results/ files (LOG-04, NFR-06)
    python -m optimization --help

Never used by the real-time controllers: it only produces recommendations that Control
Center must approve (OPT-06). Wall-clock time is read only here, to time Dispatch (D-67).
"""
