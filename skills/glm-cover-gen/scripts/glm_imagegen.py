#!/usr/bin/env python3
"""GLM（智谱 cogview-4）文生图 CLI —— 文案工厂自有 skill。

用法：
    python glm_imagegen.py "一只橘猫坐在窗台"                # 默认 864x1152（小红书 3:4 封面）
    python glm_imagegen.py "提示词" --size 1024x1024        # 方图
    python glm_imagegen.py "提示词" --n 2 --out D:/pics     # 批量 + 指定目录

鉴权（按优先级）：
    1. 环境变量 OPENAI_API_KEY / OPENAI_BASE_URL
    2. content-factory/.env 中的同名变量（GLM 文案 key 直接复用）

密钥只从环境/.env 读取，绝不写入代码、绝不打印。
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

import requests

DEFAULT_SIZE = "864x1152"  # 小红书 3:4 竖版封面
DEFAULT_MODEL = "cogview-4"
SIZES = {
    "xhs-cover": "864x1152",   # 小红书封面 3:4
    "xhs-full": "768x1344",    # 小红书整页 4:7（更瘦长）
    "square": "1024x1024",
    "wide": "1440x720",
    "banner": "1344x768",
}


def load_env_from_content_factory() -> None:
    """从 content-factory/.env 补齐未设置的 OPENAI_* 环境变量。"""
    if os.environ.get("OPENAI_API_KEY"):
        return
    env_file = Path(__file__).resolve().parents[3] / "content-factory" / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(OPENAI_BASE_URL|OPENAI_API_KEY)\s*=\s*(\S+)\s*$", line)
        if m and not os.environ.get(m.group(1)):
            os.environ[m.group(1)] = m.group(2)


def generate(prompt: str, *, size: str, model: str, n: int, timeout: int = 120) -> list[str]:
    base = (os.environ.get("OPENAI_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        sys.exit("缺少 OPENAI_API_KEY：请设置环境变量，或确认 content-factory/.env 存在该行。")
    resp = requests.post(
        f"{base}/images/generations",
        json={"model": model, "prompt": prompt, "size": size, "n": n},
        headers={"Authorization": f"Bearer {key}"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        sys.exit(f"生成失败 HTTP {resp.status_code}：{resp.text[:300]}")
    data = resp.json().get("data") or []
    urls = [d.get("url") or "" for d in data if isinstance(d, dict)]
    if not urls or not all(urls):
        sys.exit(f"响应里没有图片 URL：{str(resp.json())[:300]}")
    return urls


def download(urls: list[str], out_dir: Path, prefix: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    paths = []
    for i, url in enumerate(urls, 1):
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        # cogview 返回的 URL 实际多为 JPEG，按魔数定后缀，避免假 .png
        ext = "png" if r.content[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
        path = out_dir / f"{prefix}-{stamp}-{i}.{ext}"
        path.write_bytes(r.content)
        paths.append(path)
        print(f"[ok] {path}  ({len(r.content) // 1024} KB)")
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description="GLM cogview 文生图")
    ap.add_argument("prompt", help="生图提示词")
    ap.add_argument("--size", default=DEFAULT_SIZE,
                    help=f"尺寸 WxH 或预设 {'/'.join(SIZES)}（默认 {DEFAULT_SIZE}）")
    ap.add_argument("--model", default=os.environ.get("GLM_IMAGE_MODEL", DEFAULT_MODEL))
    ap.add_argument("-n", type=int, default=1, help="生成张数（默认 1）")
    ap.add_argument("--out", default="glm_images", help="输出目录（默认 ./glm_images）")
    ap.add_argument("--prefix", default="glm", help="文件名前缀")
    args = ap.parse_args()

    load_env_from_content_factory()
    size = SIZES.get(args.size, args.size)
    print(f"[->] model={args.model} size={size} n={args.n}")
    urls = generate(args.prompt, size=size, model=args.model, n=args.n)
    download(urls, Path(args.out), args.prefix)


if __name__ == "__main__":
    main()
