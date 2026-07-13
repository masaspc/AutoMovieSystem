"""管理画面(Jinja2+HTMX)ルート。"""

from __future__ import annotations

from fastapi import APIRouter

from app.web.approvals import router as approvals_router
from app.web.benchmarks_page import router as benchmarks_router
from app.web.comments_page import router as comments_router
from app.web.dashboard import router as dashboard_router
from app.web.growth_page import router as growth_router
from app.web.help_page import router as help_router
from app.web.insights_page import router as insights_router
from app.web.jobs_page import router as jobs_router
from app.web.publications_page import router as publications_router
from app.web.reviews_page import router as reviews_router
from app.web.series_page import router as series_router
from app.web.settings_page import router as settings_router
from app.web.tasks_status import router as tasks_status_router
from app.web.topics import router as topics_router
from app.web.trends_page import router as trends_router
from app.web.usage_page import router as usage_router
from app.web.video_projects import router as video_projects_router

router = APIRouter(tags=["web"])
router.include_router(help_router)
router.include_router(dashboard_router)
router.include_router(growth_router)
router.include_router(benchmarks_router)
router.include_router(trends_router)
router.include_router(topics_router)
router.include_router(series_router)
router.include_router(video_projects_router)
router.include_router(approvals_router)
router.include_router(reviews_router)
router.include_router(publications_router)
router.include_router(comments_router)
router.include_router(insights_router)
router.include_router(jobs_router)
router.include_router(usage_router)
router.include_router(settings_router)
router.include_router(tasks_status_router)
