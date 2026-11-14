"""Application wiring (owned by An).

    python -m app            web server + UI (needs Huy's api/ package)
    python -m app.headless   run a scenario as fast as possible, print key events, replay

Only this package reads the wall clock (``app/runner.py``); ``simulation/`` never does.
"""
