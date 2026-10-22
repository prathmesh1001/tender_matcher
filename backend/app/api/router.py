"""Aggregate every route module into a single `api_router`.

This is the only module that includes route files. A new resource gets its own file under
`api/routes/` and one `include_router` line here.
"""

from fastapi import APIRouter

from app.api.routes import health

api_router = APIRouter()
api_router.include_router(health.router)
