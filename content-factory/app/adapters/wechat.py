"""微信公众号适配层（P9）：封面渲染与 assets 登记。

图片部分：render_cover_asset 调共享图文服务 services/imaging.py 的
render_wechat_cover（PIL 本地排版 900×383，零计费，不涉 AI 生图总闸
CF_IMAGEGEN_ENABLED）；本模块不自己画图、不调 LLM、不调公众号接口。
"""
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .. import config
from ..models import Asset
from ..services import imaging


def render_cover_asset(session: Session, article_id: int, cover_text: str) -> int:
    """公众号封面出图（1 张 900×383）并登记 assets 行。

    在调用方的落库事务内执行（SDD 5.7：articles 与 assets 同一事务，
    不留"有文章无资产"的半成品）；imaging 抛 ImagingError 时由调用方把
    article 落 failed。cover_text 首选 digest（≤54 字），缺省回退标题。
    重复渲染幂等：清 data/assets/{article_id}/ 旧文件，再删旧 assets 行重建。
    """
    out_dir: Path = config.ASSETS_DIR / str(article_id)
    if out_dir.exists():
        for old in out_dir.iterdir():
            if old.is_file():
                old.unlink()
    rendered = imaging.render_wechat_cover(cover_text, out_dir / "01_cover.png")
    session.execute(delete(Asset).where(Asset.article_id == article_id))
    session.add(
        Asset(
            article_id=article_id,
            kind="cover",
            path=f"assets/{article_id}/{rendered.filename}",
            width=rendered.width,
            height=rendered.height,
        )
    )
    return 1
