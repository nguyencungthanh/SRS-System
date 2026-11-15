"""HTTP + WebSocket API (Huy, HY-05). ``app/__main__.py`` calls ``create_app(runner)``."""

from api.app import create_app

__all__ = ["create_app"]
