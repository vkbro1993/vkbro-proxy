"""
Vkbro-MLX Agent Cache Proxy v2.7.0 — 状态管理模块
=================================================
ProxyState 单例类，替代原 _state 全局 dict + _checkpoint_count + _log_buffer + _MAX_LOG。
所有状态变更通过 threading.Lock 保护。
"""

import threading
from datetime import datetime
from typing import Any, Optional


_MUTEX = threading.Lock()


class ProxyState:
    """代理全局状态 — 单例，线程安全。"""

    _instance: Optional["ProxyState"] = None

    def __new__(cls) -> "ProxyState":
        if cls._instance is None:
            with _MUTEX:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialised = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialised:
            return
        self._initialised = True
        self._lock = threading.Lock()

        # ---- 锚定块状态 ----
        self.frozen_prefix_hash: Optional[str] = None
        self.frozen_system_msgs: Optional[list] = None
        self.frozen_message_count: int = 0
        self.frozen_token_count: int = 0
        self.orphaned_system_msgs: list = []
        self.pre_freeze_turns: int = 0
        self.frozen_tools_hash: Optional[str] = None
        self.frozen_tools_count: int = 0
        self._frozen_tools_msg: Optional[dict] = None

        # ---- 对话块状态 ----
        self.ephemeral_buffer: list = []
        self.request_history: list = []
        self.cache_state: str = "HEALTHY"
        self.hit_rate_window: list = []
        self.stream_miss_count: int = 0
        self.fallback_mode: bool = False
        self.last_compression_round: int = 0
        self.total_turns: int = 0
        self.last_prompt_tokens: int = 0
        self.last_cached_tokens: int = 0
        self.ephemeral_compressed: bool = False

        # ---- 消费追踪 ----
        self._consumed_skill_ids: set = set()
        self._consumed_tool_ids: set = set()

        # ---- 块统计 ----
        self._stable_hot_blocks: int = 0
        self._stable_indexed_blocks: int = 0
        self._last_auto_checkpoint: int = 0
        self._last_ephemeral_tokens: int = 0
        self._last_total_tokens: int = 0

        # ---- 截断状态 ----
        self._cut_before_turn: Optional[int] = None
        self._cut_summary: str = ""

        # ---- 非 system 锚点标记 ----
        self._anchor_from_non_system: bool = False

        # ---- 日志 ----
        self._checkpoint_count: int = 0
        self._log_buffer: list = []
        self._MAX_LOG: int = 100

        # ---- MLX 轮询状态 ----
        self._last_mlx_prompt_total: int = 0
        self._last_mlx_cache_total: int = 0

        # ---- 模型缓存 ----
        self._last_model_cache: dict = {"model": "Qwen3.6-35B-A3B-MLX-6bit", "time": 0}

    # ── 日志 ────────────────────────────────────────────────────────

    def log(self, msg: str) -> None:
        """记录一条带时间戳的日志。"""
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        with self._lock:
            self._log_buffer.append(entry)
            if len(self._log_buffer) > self._MAX_LOG:
                self._log_buffer.pop(0)
        print(entry, flush=True)

    # ── 检查点计数 ──────────────────────────────────────────────────

    @property
    def checkpoint_count(self) -> int:
        with self._lock:
            return self._checkpoint_count

    def inc_checkpoint(self) -> int:
        with self._lock:
            self._checkpoint_count += 1
            return self._checkpoint_count

    # ── reset ───────────────────────────────────────────────────────

    def reset_anchor(self) -> None:
        """完全重置锚点状态（对应 /reset-anchor 路由）。"""
        with self._lock:
            self.frozen_prefix_hash = None
            self.frozen_system_msgs = None
            self.pre_freeze_turns = 0
            self.ephemeral_compressed = False
            self.ephemeral_buffer = []
            self.hit_rate_window = []
            self._consumed_skill_ids = set()
            self._consumed_tool_ids = set()
            self.frozen_tools_hash = None
            self.frozen_tools_count = 0
            self._frozen_tools_msg = None
            self._cut_before_turn = None
            self._cut_summary = ""

    # ── 读取工具 — 线程安全 ────────────────────────────────────────

    def get_logs(self, n: int = 50) -> list:
        with self._lock:
            return list(self._log_buffer[-n:])

    def snapshot(self) -> dict[str, Any]:
        """返回只读快照（用于 dashboard / health）。"""
        with self._lock:
            wa = (sum(self.hit_rate_window) / len(self.hit_rate_window)
                  if self.hit_rate_window else 0)
            return {
                "frozen_prefix_hash": self.frozen_prefix_hash,
                "frozen_system_msgs": self.frozen_system_msgs,
                "frozen_message_count": self.frozen_message_count,
                "frozen_token_count": self.frozen_token_count,
                "orphaned_system_msgs": self.orphaned_system_msgs,
                "pre_freeze_turns": self.pre_freeze_turns,
                "ephemeral_buffer": self.ephemeral_buffer,
                "request_history": list(self.request_history),
                "cache_state": self.cache_state,
                "hit_rate_window": list(self.hit_rate_window),
                "stream_miss_count": self.stream_miss_count,
                "fallback_mode": self.fallback_mode,
                "last_compression_round": self.last_compression_round,
                "total_turns": self.total_turns,
                "last_prompt_tokens": self.last_prompt_tokens,
                "last_cached_tokens": self.last_cached_tokens,
                "ephemeral_compressed": self.ephemeral_compressed,
                "frozen_tools_count": self.frozen_tools_count,
                "frozen_tools_hash": self.frozen_tools_hash,
                "_frozen_tools_msg": self._frozen_tools_msg,
                "_stable_hot_blocks": self._stable_hot_blocks,
                "_stable_indexed_blocks": self._stable_indexed_blocks,
                "_last_auto_checkpoint": self._last_auto_checkpoint,
                "_last_ephemeral_tokens": self._last_ephemeral_tokens,
                "_last_total_tokens": self._last_total_tokens,
                "_cut_before_turn": self._cut_before_turn,
                "_cut_summary": self._cut_summary,
                "window_avg_hit_rate": round(wa, 4),
            }
