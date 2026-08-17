"""FastAPI 入口：挂载路由与调度器（计划书第 3 章）。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
RUN_SCHEDULER=0 可只起 API 不起定时任务（调试用）。
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import config
from .api import routes_admin, routes_articles, routes_pages, routes_prompts, routes_topics
from .db import init_db
from .services import prompt_engine
from .services.scheduler import create_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 种子模板幂等入库（M4）：重启绝不覆盖库内已改模板
    seeded = prompt_engine.seed_prompts()
    if seeded:
        logging.getLogger(__name__).info("种子模板入库：%s", seeded)
    scheduler = None
    if config.RUN_SCHEDULER:
        scheduler = create_scheduler()
        scheduler.start()
        logging.getLogger(__name__).info("调度器已启动：%s", [j.id for j in scheduler.get_jobs()])
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="content-factory", version="0.1.0", lifespan=lifespan)
    app.include_router(routes_admin.router)
    app.include_router(routes_topics.router)
    app.include_router(routes_articles.router)
    app.include_router(routes_prompts.router)
    app.include_router(routes_pages.router)

    @app.get("/static/assets/{article_id}/{filename:path}", include_in_schema=False)
    def asset_file(article_id: int, filename: str) -> FileResponse:
        """只读提供某文章目录下的单个素材，拒绝目录穿越。"""
        if article_id < 1 or not filename or "/" in filename or "\\" in filename:
            raise HTTPException(status_code=404, detail="素材不存在")
        root = config.ASSETS_DIR.resolve()
        target = (root / str(article_id) / filename).resolve()
        try:
            target.relative_to(root / str(article_id))
        except ValueError:
            raise HTTPException(status_code=404, detail="素材不存在") from None
        if not target.is_file():
            raise HTTPException(status_code=404, detail="素材不存在")
        return FileResponse(target)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
