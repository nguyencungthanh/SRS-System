"""Fake modules (framework F6), owned by An.

Simple behaviour, correctly shaped messages, SRS timing (2 s per floor, 6 s per stop), so a
system made only of fakes reproduces the AT-01 timeline. Each file exports the same
``create`` factory as the real package it stands in for (``contracts.interfaces``).

    fakes.cabin.create(init)                 stands in for cabin.create(init)
    fakes.dispatch.create(policy, algorithm) stands in for dispatch.create(policy, algorithm)
    fakes.control_center.create()            stands in for control_center.create()

Fakes are NOT reference implementations. Do not copy their logic into a real module.
"""
