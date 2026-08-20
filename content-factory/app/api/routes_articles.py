"""文章读取、素材包下载与发布回填接口（P3 / M8；P4 回填后触发评分重算）。

P8a 增量：小红书文章发布前的 RedFox 违禁词体检（手动、按调用计费），
命中词可一键回填本地词表（下次生成直接拦截，滚动扩充）。
"""
import logging
from datetime import datetime
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import config
from ..collectors.redfox import RedFoxError
from ..db import session_scope
from ..models import Article, Asset, PublishRecord, Topic
from ..services import scoring, sensitive

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["articles"])


def _article_dict(article: Article, assets: list[Asset] | None = None) -> dict:
    data = {
        "id": article.id,
        "topic_id": article.topic_id,
        "prompt_id": article.prompt_id,
        "platform": article.platform,
        "title": article.title,
        "content": article.content,
        "tags": article.tags or [],
        "meta": article.meta or {},
        "status": article.status,
        "error": article.error,
        "created_at": article.created_at,
    }
    if assets is not None:
        data["assets"] = [
            {
                "id": asset.id,
                "kind": asset.kind,
                "path": asset.path,
                "width": asset.width,
                "height": asset.height,
                "created_at": asset.created_at,
            }
            for asset in assets
        ]
    return data


@router.get("/articles")
def list_articles(
    topic_id: int | None = None,
    limit: int = Query(500, ge=1, le=2000, description="分页上限，防全表膨胀拖垮响应"),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    """列出文章，供管理页与同源页面脚本读取。"""
    with session_scope() as session:
        statement = select(Article).order_by(Article.created_at.desc(), Article.id.desc())
        if topic_id is not None:
            statement = statement.where(Article.topic_id == topic_id)
        statement = statement.limit(limit).offset(offset)
        return [_article_dict(article) for article in session.scalars(statement).all()]


@router.get("/articles/{article_id}")
def get_article(article_id: int) -> dict:
    with session_scope() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail=f"article {article_id} 不存在")
        assets = list(
            session.scalars(
                select(Asset).where(Asset.article_id == article_id).order_by(Asset.id)
            )
        )
        return _article_dict(article, assets)


@router.get("/articles/{article_id}/package")
def download_package(article_id: int) -> StreamingResponse:
    """按 SDD 4.2 在内存中创建小红书 ready 素材包。"""
    with session_scope() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail=f"article {article_id} 不存在")
        assets = list(
            session.scalars(
                select(Asset).where(Asset.article_id == article_id).order_by(Asset.id)
            )
        )
        if article.platform != "xhs":
            raise HTTPException(status_code=409, detail="仅小红书文章可下载素材包")
        if article.status != "ready":
            raise HTTPException(status_code=409, detail="仅 ready 文章可下载素材包")
        if not assets:
            raise HTTPException(status_code=409, detail="文章没有可下载的素材")

        package = BytesIO()
        with ZipFile(package, "w", ZIP_DEFLATED) as archive:
            archive.writestr("title.txt", article.title)
            archive.writestr("content.txt", article.content)
            for index, asset in enumerate(assets, start=1):
                # path 形如 "assets/{article_id}/{file}"（相对 data/）；解析时尊重
                # ASSETS_DIR 覆盖，避免测试/部署改了目录后读到别处的文件
                rel = asset.path
                if rel.startswith("assets/"):
                    rel = rel[len("assets/"):]
                source = config.ASSETS_DIR / rel
                # assets 表的 path 是受控写入的路径；丢失文件不能生成残包。
                if not source.is_file():
                    raise HTTPException(status_code=409, detail=f"素材文件不存在：{asset.path}")
                suffix = source.suffix or ".png"
                archive.writestr(f"images/{index:02d}_{asset.kind}{suffix}", source.read_bytes())
        package.seek(0)

    return StreamingResponse(
        package,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="article-{article_id}-package.zip"'},
    )


@router.post("/articles/{article_id}/sensitive-check")
def sensitive_check(article_id: int) -> dict:
    """发布前违禁词体检：标题 + 正文全文交 RedFox 小红书违禁词库检测。

    按调用计费——只由文章页手动触发（页面 confirm 后才发请求），不进任何
    自动链路。结果不落库：体检是发布前的人工质检动作，词表回填才是持久化。
    """
    # 延迟导入：collectors.redfox 依赖 httpx，P3 离线验收不触发本接口
    from ..collectors import redfox

    with session_scope() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail=f"article {article_id} 不存在")
        if article.platform != "xhs":
            raise HTTPException(
                status_code=409, detail="违禁词体检目前仅支持小红书文章（RedFox 小红书词库）"
            )
        text = f"{article.title}\n\n{article.content}"
    try:
        result = redfox.sensitive_word_search(text)
    except RedFoxError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return {
        "article_id": article_id,
        "checked_chars": len(text),
        "word_count": len(result["words"]),
        "words": result["words"],
        "categories": result["categories"],
        "billed": True,
    }


class SensitiveWordsIn(BaseModel):
    """命中词回填本地词表：单批 ≤ 50 词，单词 ≤ 50 字（防误杀整站的超短词在服务层拦）。"""

    words: list[str] = Field(..., min_length=1, max_length=50)


@router.post("/sensitive/{platform}/words", status_code=201)
def add_sensitive_words(platform: str, payload: SensitiveWordsIn) -> dict:
    """把体检命中的词追加进平台敏感词表（文件追加、即时生效、去重）。

    滚动扩充词表的既定路径（sensitive_xhs.txt 头注释）：命中词经人工确认后
    入表，下次生成直接在本地方向拦截，不再依赖每次付费体检。
    """
    if platform not in ("xhs", "wechat"):
        raise HTTPException(status_code=422, detail=f"未知平台 {platform}（支持 xhs / wechat）")
    added, skipped = sensitive.add_words(platform, payload.words)
    return {"platform": platform, "added": added, "skipped": skipped}


class PublishMetrics(BaseModel):
    """回填互动数据：非负、封顶 10^9（防脏数据/溢出写进 publish_records）。"""

    model_config = {"extra": "allow"}  # 未来加字段（如 shares）不破坏旧调用方

    likes: int = Field(default=0, ge=0, le=10**9)
    collects: int = Field(default=0, ge=0, le=10**9)
    comments: int = Field(default=0, ge=0, le=10**9)


class PublishRequest(BaseModel):
    platform: str
    account: str = Field(default="", max_length=100)
    url: str | None = Field(default=None, max_length=1000)
    metrics: PublishMetrics = Field(default_factory=PublishMetrics)


@router.post("/articles/{article_id}/publish", status_code=201)
def publish_article(article_id: int, payload: PublishRequest) -> dict:
    """追加一条发布回填记录，并把文章置为 published 终态。

    409 = 文章非 ready/published 状态（archived/failed 行不能回填，先重新生成）；
    422 = 回填 platform 与文章 platform 不一致（归因错位会污染双端报表）。
    P4 副作用：回填事务提交后触发 scoring.recompute()，让回填数据即时反哺
    topics.score 与排序。重算失败不影响已提交的回填（只记日志），可手动重算补齐。
    """
    with session_scope() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail=f"article {article_id} 不存在")
        if article.status not in ("ready", "published"):
            raise HTTPException(
                status_code=409,
                detail=f"仅 ready/published 文章可回填，当前 status={article.status}；请先重新生成",
            )
        if payload.platform != article.platform:
            raise HTTPException(
                status_code=422,
                detail=f"回填 platform={payload.platform} 与文章 platform={article.platform} 不一致",
            )
        record = PublishRecord(
            article_id=article.id,
            platform=payload.platform,
            account=payload.account,
            url=payload.url,
            metrics=payload.metrics.model_dump(),
        )
        session.add(record)
        article.status = "published"
        session.flush()
        result = {
            "id": record.id,
            "article_id": record.article_id,
            "platform": record.platform,
            "account": record.account,
            "url": record.url,
            "metrics": record.metrics or {},
            "published_at": record.published_at or datetime.now(),
            "status": article.status,
        }

    try:
        result["scoring"] = scoring.recompute()
    except Exception:
        # 回填已提交，评分重算是纯确定性计算，失败可随时手动补跑
        logger.exception("publish 后 topics.score 重算失败（article=%s）", article_id)
        result["scoring"] = {"error": "重算失败，请手动触发 recompute"}
    return result
