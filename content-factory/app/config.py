"""环境变量与常量（计划书第 3、4、6、7 章）。

密钥只出现在 .env（不入库）；.env 中已有的值不覆盖进程环境变量。
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(PROJECT_ROOT / ".env")

# ---- 数据层 ----
DATA_DIR = Path(os.environ.get("CF_DATA_DIR", str(PROJECT_ROOT / "data")))
DB_PATH = Path(os.environ.get("CF_DB_PATH", str(DATA_DIR / "app.db")))
BACKUP_DIR = Path(os.environ.get("CF_BACKUP_DIR", str(DATA_DIR / "backups")))
DOMAINS_FILE = Path(os.environ.get("CF_DOMAINS_FILE", str(DATA_DIR / "domains.yml")))
TEMPLATES_DIR = Path(os.environ.get("CF_TEMPLATES_DIR", str(PROJECT_ROOT / "app" / "templates")))

# ---- 热榜采集（M1）----
# RSSHub 基地址。自建实例替换之；离线调试可指向本地 RSS fixture 目录，
# 形如 file:///abs/path/to/fixtures（按 {source}.xml 读取，走同一套解析代码）。
RSSHUB_BASE_URL = os.environ.get("RSSHUB_BASE_URL", "https://rsshub.app").rstrip("/")
HOTBOARD_SOURCES: dict[str, dict[str, str]] = {
    "weibo": {"route": "/weibo/search/hot", "label": "微博热搜"},
    "zhihu": {"route": "/zhihu/hotlist", "label": "知乎热榜"},
    "baidu": {"route": "/baidu/topwords", "label": "百度热搜"},
}
HTTP_TIMEOUT_SECONDS = 15
# RSS 报文体积上限（字符）：xml.etree 无实体展开防护，超限/DTD 报文直接拒绝
RSS_MAX_XML_CHARS = 2_000_000

# ---- 低粉爆款三阈值（第 6.2 节初版；环境变量可热改；P4 起按回填数据校准，
# 校准结论记录在 docs/p4-calibration.md，改值即改该文档）----
VIRAL_FANS_MAX = int(os.environ.get("CF_VIRAL_FANS_MAX", "5000"))
VIRAL_LIKES_MIN = int(os.environ.get("CF_VIRAL_LIKES_MIN", "500"))  # 自动采样候选预筛；人工样本不做该预筛
VIRAL_SCORE_MIN = float(os.environ.get("CF_VIRAL_SCORE_MIN", "2.0"))

# ---- 选题雷达（部分 M3，P-1a）----
# 撞题阈值初值；P4 起按回填数据校准，结论记录在 docs/p4-calibration.md
TOPIC_JACCARD_THRESHOLD = float(os.environ.get("CF_TOPIC_DUPLICATE_JACCARD", "0.5"))
TOPIC_DEDUP_WINDOW_DAYS = 7  # 撞题回看窗口
TOPIC_TTL_HOURS = 72  # radar 选题保鲜：created_at + 72h

# ---- topics.score 评分公式（P4 数据飞轮；公式与拍板记录见 docs/p4-calibration.md）----
# score = base_score + SCORE_EFFECT_SCALE × log1p(Σ(likes×W_L + collects×W_C + comments×W_M))
# 求和范围 = 该 topic 全部已发布文章的 publish_records.metrics；权重与 viral_score 同构。
SCORE_W_LIKES = float(os.environ.get("CF_SCORE_W_LIKES", "1"))
SCORE_W_COLLECTS = float(os.environ.get("CF_SCORE_W_COLLECTS", "2"))
SCORE_W_COMMENTS = float(os.environ.get("CF_SCORE_W_COMMENTS", "3"))
SCORE_EFFECT_SCALE = float(os.environ.get("CF_SCORE_EFFECT_SCALE", "1.0"))  # 效果分整体缩放
# 模板效果分样本量门槛：published < 该值只展示、不给出任何启停建议
PROMPT_STATS_MIN_SAMPLES = int(os.environ.get("CF_PROMPT_STATS_MIN_SAMPLES", "10"))

# ---- 数据保留与备份（第 5 章 / 第 10 章协作纪律）----
HOT_ITEMS_RETENTION_DAYS = 90  # hot_items 只保留 90 天，周清理任务物理删除
BACKUP_KEEP = 7  # 每日备份保留最近 7 份

# ---- 告警（第 7 章横切约定）----
NOTIFY_WEBHOOK = os.environ.get("NOTIFY_WEBHOOK", "")
COLLECTOR_FAIL_ALERT_AFTER = 3  # 连续失败达到该次数即外发告警
COLLECTOR_CIRCUIT_FAILURES = int(os.environ.get("CF_COLLECTOR_CIRCUIT_FAILURES", "3"))  # 连续失败达到该次数即熔断

# ---- 小红书采样器 M2（P-1b；只读搜索，禁止任何写/互动接口）----
# xiaohongshu-mcp 以 Go 独立服务跑在本机 Docker，streamable-http 端点 /mcp
XHS_MCP_BASE_URL = os.environ.get("XHS_MCP_BASE_URL", "http://localhost:18060").rstrip("/")
XHS_MCP_TIMEOUT_SECONDS = int(os.environ.get("CF_XHS_MCP_TIMEOUT", "30"))
# 每轮搜索关键词；留空则取 data/domains.yml 全部领域关键词作为检索词
XHS_SAMPLE_KEYWORDS = [k.strip() for k in os.environ.get("CF_XHS_SAMPLE_KEYWORDS", "").split(",") if k.strip()]
XHS_SAMPLE_MAX_QUERIES = int(os.environ.get("CF_XHS_SAMPLE_MAX_QUERIES", "20"))
XHS_SAMPLE_INTERVAL_HOURS = int(os.environ.get("CF_XHS_SAMPLE_INTERVAL_HOURS", "6"))
# 周度 LLM 拆解（A3）：调度周期 cron 化，周一 06:00（本地时区）
XHS_TEARDOWN_WEEKDAY = os.environ.get("CF_XHS_TEARDOWN_WEEKDAY", "mon")
XHS_TEARDOWN_HOUR = int(os.environ.get("CF_XHS_TEARDOWN_HOUR", "6"))

# ---- RedFox（红狐数据 redfox.hk）：小红书只读数据源，按调用计费 ----
# 配置 Key 即启用 RedFox 优先采样（结果自带 authorFans，低粉爆款可直接判定），
# 无 Key 或调用失败自动降级 xiaohongshu-mcp。接口文档见仓库外 redfox-api文档/
REDFOX_API_KEY = (os.environ.get("CF_REDFOX_API_KEY") or os.environ.get("REDFOX_API_KEY") or "").strip()
REDFOX_BASE_URL = os.environ.get("CF_REDFOX_BASE_URL", "https://redfox.hk").rstrip("/")
REDFOX_TIMEOUT_SECONDS = int(os.environ.get("CF_REDFOX_TIMEOUT", "30"))
REDFOX_WINDOW_DAYS = int(os.environ.get("CF_REDFOX_WINDOW_DAYS", "7"))

# ---- 运行开关 ----
RUN_SCHEDULER = os.environ.get("RUN_SCHEDULER", "1") != "0"

# ---- LLM（M5 产品内，P0 起；计划书第 13.4 节）----
# OpenAI 兼容协议，客户端只依赖下面三个环境变量，供应商切换不改代码。
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com").rstrip("/")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL_NAME = os.environ.get("MODEL_NAME", "deepseek-chat")

# 成本与超时控制（第 6.4 / 8.3 节，超时值集中在此，建议 60s 可热改）
# 思维链模型（glm-4.x）思考与正文共享 max_tokens，给足余量防正文被截空
LLM_MAX_TOKENS = int(os.environ.get("CF_LLM_MAX_TOKENS", "8192"))
LLM_TIMEOUT_SECONDS = int(os.environ.get("CF_LLM_TIMEOUT_SECONDS", "120"))
# 思维链开关（bigmodel GLM-4.5+ 的 thinking 参数）：产物是结构化 JSON 文案，
# 默认关掉思考段省 token 提速。auto=仅 glm 系模型带此参数；true/false 强制
_thinking_mode = os.environ.get("CF_LLM_DISABLE_THINKING", "auto").lower()
LLM_DISABLE_THINKING = _thinking_mode in ("true", "1", "yes") or (
    _thinking_mode == "auto" and MODEL_NAME.lower().startswith("glm")
)
LLM_MAX_RETRIES = 2  # 重试封顶 2 次（1 次首发 + 2 次重试 = 最多 3 轮）
LLM_RETRY_BACKOFF_SECONDS = float(os.environ.get("CF_LLM_RETRY_BACKOFF_SECONDS", "2"))  # 重试间隔（固定退避）
# 单价（美元 / 每百万 token），用于 meta.usage.cost_est 估算；换供应商时改这里。
# ⚠️ P4 成本报表口径：当前默认为 DeepSeek 单价，切 GLM 后必须用
# CF_LLM_PRICE_INPUT / CF_LLM_PRICE_OUTPUT 按官方价修正，否则报表只能作"估算口径"。
LLM_PRICE_INPUT_PER_M = float(os.environ.get("CF_LLM_PRICE_INPUT", "0.30"))
LLM_PRICE_OUTPUT_PER_M = float(os.environ.get("CF_LLM_PRICE_OUTPUT", "0.50"))

# 无 Key 降级（P0 验收期脚手架）：未配置 OPENAI_API_KEY 或显式 CF_LLM_MOCK=1 时走 mock。
# mock 仅用于跑通链路结构，真实 Key 配置后自动走真实 HTTP 调用，两路径并列、由开关分流。
LLM_MOCK = os.environ.get("CF_LLM_MOCK", "0") == "1" or not OPENAI_API_KEY

# ---- 图文合成（M6 / M7 共用共享服务，P2 起；计划书 6.3）----
# 版式即数据：画布/背景/字体/字号/槽位/色值全部在 data/imaging_templates/*.yml，不散落代码。
FONTS_DIR = Path(os.environ.get("CF_FONTS_DIR", str(DATA_DIR / "fonts")))
IMAGING_TEMPLATES_DIR = Path(
    os.environ.get("CF_IMAGING_TEMPLATES_DIR", str(DATA_DIR / "imaging_templates"))
)
ASSETS_DIR = Path(os.environ.get("CF_ASSETS_DIR", str(DATA_DIR / "assets")))
IMAGING_COVER_TEMPLATE = os.environ.get("CF_IMAGING_COVER_TEMPLATE", "emotion_cover")
IMAGING_QUOTE_TEMPLATE = os.environ.get("CF_IMAGING_QUOTE_TEMPLATE", "quote_card")
IMAGING_MIN_FONT_SIZE = int(os.environ.get("CF_IMAGING_MIN_FONT_SIZE", "28"))  # 超长缩字号的下限

# ---- 敏感词表（第 6.2 节，双端词表分离）----
SENSITIVE_FILE_WECHAT = Path(os.environ.get("CF_SENSITIVE_WECHAT", str(DATA_DIR / "sensitive_wechat.txt")))
SENSITIVE_FILE_XHS = Path(os.environ.get("CF_SENSITIVE_XHS", str(DATA_DIR / "sensitive_xhs.txt")))
