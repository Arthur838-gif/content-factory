"""文章读取、素材包下载与发布回填接口（P3 / M8；P4 回填后触发评分重算）。"""
import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import config
from ..db import session_scope
from ..models import Article, Asset, PublishRecord, Topic
from ..services import scoring

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
def list_articles(topic_id: int | None = None) -> list[dict]:
    """列出文章，供管理页与同源页面脚本读取。"""
    with session_scope() as session:
        statement = select(Article).order_by(Article.created_at.desc(), Article.id.desc())
        if topic_id is not None:
            statement = statement.where(Article.topic_id == topic_id)
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
                source = Path(config.DATA_DIR) / asset.path
                # assets 表的 path 是受控写入的相对 data/ 路径；丢失文件不能生成残包。
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


class PublishRequest(BaseModel):
    platform: str
    account: str = ""
    url: str | None = None
    metrics: dict = Field(default_factory=dict)


@router.post("/articles/{article_id}/publish", status_code=201)
def publish_article(article_id: int, payload: PublishRequest) -> dict:
    """追加一条发布回填记录，并把文章置为 published 终态。

    P4 副作用：回填事务提交后触发 scoring.recompute()，让回填数据即时反哺
    topics.score 与排序。重算失败不影响已提交的回填（只记日志），可手动重算补齐。
    """
    with session_scope() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail=f"article {article_id} 不存在")
        record = PublishRecord(
            article_id=article.id,
            platform=payload.platform,
            account=payload.account,
            url=payload.url,
            metrics=payload.metrics,
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
