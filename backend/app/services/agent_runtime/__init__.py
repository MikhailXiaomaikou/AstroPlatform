"""Agent runtime extracted from backend/app/api/chat.py (2026-07-03 split).

The FastAPI router, endpoint handlers, request/response models, and thin
glue remain in app/api/chat.py, which also re-exports every public symbol
from these modules so existing ``from app.api.chat import ...`` imports and
test monkeypatches keep working.  Do not import app.api.chat at module level
from here (api <-> services cycle); use call-time lazy imports instead.
"""
