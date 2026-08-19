"""FastAPI 入口：挂载路由与调度器（计划书第 3 章）。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
RUN_SCHEDULER=0 可只起 API 不起定时任务（调试用）。
"""
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import config
from .api import (
    routes_admin,
    routes_articles,
    routes_discovery,
    routes_domains,
    routes_pages,
    routes_pillars,
    routes_prompts,
    routes_sampling,
    routes_stats,
    routes_topics,
    routes_viral_samples,
)
from .db import init_db
from .services import domain_service, prompt_engine
from .services.scheduler import create_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 领域种子幂等导入（P-2）：domains.yml 只作导入源，库内是唯一事实源
    seeded = domain_service.seed_domains()
    logging.getLogger(__name__).info("领域词表就绪：%s", seeded)
    # 种子模板幂等入库（M4）：重启绝不覆盖库内已改模板
    seeded_prompts = prompt_engine.seed_prompts()
    if seeded_prompts:
        logging.getLogger(__name__).info("种子模板入库：%s", seeded_prompts)
    scheduler = None
    if config.RUN_SCHEDULER:
        scheduler = create_scheduler()
        scheduler.start()
        logging.getLogger(__name__).info("调度器已启动：%s", [j.id for j in scheduler.get_jobs()])
    # 内嵌采样 worker（单机开发模式；长期部署改独立进程 + CF_WORKER_EMBEDDED=0）。
    # 不挂在 RUN_SCHEDULER 分支下：手动模式（关定时省费）仍要有 worker 消费手动任务。
    if config.WORKER_EMBEDDED:
        from .services.worker import run_embedded_worker

        run_embedded_worker()
        logging.getLogger(__name__).info("内嵌 sampling worker 已启动")
    yield
    # worker 线程是 daemon：随进程退出；显式停一下让测试/优雅停机不摸旧连接
    from .services.worker import stop_embedded_worker

    stop_embedded_worker()
    if scheduler is not None:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="content-factory", version="0.1.0", lifespan=lifespan)

    # 本机单用户服务（部署约定：只绑 127.0.0.1）。无 Cookie 会话，CSRF 面主要在
    # "恶意页面借本机浏览器发跨站 POST"（DNS rebinding / 钓鱼页）——用 Host 白名单
    # 挡 rebinding、Origin 白名单挡跨站写。"testserver" 是 FastAPI TestClient 的
    # 默认 Host，放行以保住测试链路。
    allowed_hosts = {"127.0.0.1", "localhost", "testserver", "::1"}

    @app.middleware("http")
    async def guard_local_only(request: Request, call_next):
        raw_host = (request.headers.get("host") or "").strip().lower()
        # "127.0.0.1:8000" 去端口；"[::1]:8000" 去端口再去方括号
        host = raw_host.rsplit(":", 1)[0] if raw_host.startswith("[") else raw_host.split(":")[0]
        host = host.strip("[]")
        if host not in allowed_hosts:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin:
            origin_host = (urlparse(origin).hostname or "").lower()
            if origin_host and origin_host not in allowed_hosts:
                return JSONResponse({"detail": "forbidden origin"}, status_code=403)
        response = await call_next(request)
        # 页面是实时状态（最新生成/排期/主题），浏览器 HTTP 缓存或后退缓存
        # 展示旧页会让人误以为没生效——HTML 一律 no-store 强制回源
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(routes_admin.router)
    app.include_router(routes_topics.router)
    app.include_router(routes_articles.router)
    app.include_router(routes_prompts.router)
    app.include_router(routes_stats.router)
    app.include_router(routes_viral_samples.router)
    app.include_router(routes_pillars.router)
    app.include_router(routes_discovery.router)
    app.include_router(routes_domains.router)
    app.include_router(routes_sampling.router)
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
