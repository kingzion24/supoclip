"""
Discover API routes: what is trending, and YouTube search for videos to clip.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...discover import search_youtube
from ...trends import get_trends
from ...utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/discover", tags=["discover"])


@router.get("/trending")
async def trending(
    request: Request,
    region: str | None = Query(default=None, min_length=2, max_length=2),
    db: AsyncSession = Depends(get_db),
):
    """Trending searches (Google Trends) and popular YouTube videos for a country."""
    await resolve_authenticated_user_id(request, db, get_config())
    return await run_in_thread(get_trends, region)


@router.get("/search")
async def search(
    request: Request,
    q: str = Query(min_length=2, max_length=120),
    limit: int = Query(default=12, ge=1, le=25),
    kind: str = Query(default="videos", pattern="^(videos|podcasts)$"),
    min_minutes: int = Query(default=0, ge=0, le=180),
    db: AsyncSession = Depends(get_db),
):
    """Search YouTube for videos or podcast episodes to clip."""
    await resolve_authenticated_user_id(request, db, get_config())
    try:
        results = await run_in_thread(
            search_youtube, q, limit, kind == "podcasts", min_minutes
        )
    except Exception as exc:
        logger.warning("YouTube search failed for %r: %s", q, exc)
        raise HTTPException(
            status_code=502, detail="YouTube search is unavailable right now. Try again shortly."
        )
    return {"query": q, "kind": kind, "results": results}
