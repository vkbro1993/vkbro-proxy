"""
Vkbro-MLX Agent Cache Proxy v2.7.0 — 配置模块
==============================================
支持 .env 文件覆盖所有硬编码默认值。
"""

import os

# ---------------------------------------------------------------------------
# .env 加载 (可选依赖)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv

    _env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv 未安装时静默跳过

# ---------------------------------------------------------------------------
# OMLX 连接
# ---------------------------------------------------------------------------
OMLX_URL: str = os.getenv("OMLX_URL", "http://localhost:8001/v1/chat/completions")
OMLX_API_KEY: str = os.getenv("OMLX_API_KEY", "SK-OMLX-VKBRO")
OMLX_HEADERS: dict = {"Authorization": f"Bearer {OMLX_API_KEY}"}
OMLX_ADMIN_URL: str = os.getenv("OMLX_ADMIN_URL", "http://localhost:8001/admin/api/stats")
OMLX_MODELS_URL: str = os.getenv("OMLX_MODELS_URL", "http://localhost:8001/v1/models")
OMLX_STATUS_URL: str = os.getenv("OMLX_STATUS_URL", "http://localhost:8001/v1/status")

# ---------------------------------------------------------------------------
# Proxy 自身
# ---------------------------------------------------------------------------
PROXY_PORT: int = int(os.getenv("PROXY_PORT", "8000"))
PROXY_HOST: str = os.getenv("PROXY_HOST", "127.0.0.1")

# ---------------------------------------------------------------------------
# 缓存 / 锚点 阈值
# ---------------------------------------------------------------------------
PRE_FREEZE_DELAY: int = int(os.getenv("PRE_FREEZE_DELAY", "2"))

HIT_RATE_HEALTHY: float = float(os.getenv("HIT_RATE_HEALTHY", "0.7"))
HIT_RATE_DEGRADED: float = float(os.getenv("HIT_RATE_DEGRADED", "0.5"))
HIT_RATE_CRITICAL: float = float(os.getenv("HIT_RATE_CRITICAL", "0.3"))
WINDOW_SIZE: int = int(os.getenv("WINDOW_SIZE", "5"))

# ---------------------------------------------------------------------------
# SSE / 流式
# ---------------------------------------------------------------------------
STREAM_MISS_THRESHOLD: int = int(os.getenv("STREAM_MISS_THRESHOLD", "3"))

# ---------------------------------------------------------------------------
# 压缩
# ---------------------------------------------------------------------------
COMPRESSION_MIN_COUNT: int = int(os.getenv("COMPRESSION_MIN_COUNT", "3"))
COMPRESSION_COOLDOWN: int = int(os.getenv("COMPRESSION_COOLDOWN", "2"))
EPHEMERAL_COMPRESS_THRESHOLD: int = int(os.getenv("EPHEMERAL_COMPRESS_THRESHOLD", "4000"))
EPHEMERAL_COMPRESS_TARGET: int = int(os.getenv("EPHEMERAL_COMPRESS_TARGET", "2000"))

# ---------------------------------------------------------------------------
# 自动检查点
# ---------------------------------------------------------------------------
AUTO_CHECKPOINT_BLOCKS: int = int(os.getenv("AUTO_CHECKPOINT_BLOCKS", "40"))
AUTO_CHECKPOINT_KEEP: float = float(os.getenv("AUTO_CHECKPOINT_KEEP", "0.35"))

# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------
HTTP_TIMEOUT: float = float(os.getenv("HTTP_TIMEOUT", "180.0"))
MAX_KEEPALIVE: int = int(os.getenv("MAX_KEEPALIVE", "2"))
ADMIN_TIMEOUT: float = float(os.getenv("ADMIN_TIMEOUT", "3.0"))
