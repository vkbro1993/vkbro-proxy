"""
Vkbro-MLX Agent Cache Proxy v2.8.1 — 核心引擎
=============================================
锚点引擎、消息重组引擎、缓存健康度、自动检查点、自动压缩、SSE 解析。
所有函数接收 ProxyState 实例参数，不直接访问全局状态。
"""

import hashlib
import json
import re
import time
from typing import Any, Optional

import httpx

try:
    from .config import (
        OMLX_HEADERS,
        HIT_RATE_HEALTHY,
        HIT_RATE_DEGRADED,
        HIT_RATE_CRITICAL,
        WINDOW_SIZE,
        STREAM_MISS_THRESHOLD,
        PRE_FREEZE_DELAY,
        AUTO_CHECKPOINT_BLOCKS,
        AUTO_CHECKPOINT_KEEP,
        EPHEMERAL_COMPRESS_THRESHOLD,
        EPHEMERAL_COMPRESS_TARGET,
        ADMIN_TIMEOUT,
    )
    from .proxy_state import ProxyState
except ImportError:
    from config import (  # type: ignore[no-redef]
        OMLX_HEADERS,
        HIT_RATE_HEALTHY,
        HIT_RATE_DEGRADED,
        HIT_RATE_CRITICAL,
        WINDOW_SIZE,
        STREAM_MISS_THRESHOLD,
        PRE_FREEZE_DELAY,
        AUTO_CHECKPOINT_BLOCKS,
        AUTO_CHECKPOINT_KEEP,
        EPHEMERAL_COMPRESS_THRESHOLD,
        EPHEMERAL_COMPRESS_TARGET,
        ADMIN_TIMEOUT,
    )
    from proxy_state import ProxyState  # type: ignore[no-redef]


# ============================================================
# 锚点引擎
# ============================================================

def extract_system_messages(messages: list[dict]) -> list[dict]:
    """提取所有 role=system 的消息。"""
    return [m for m in messages if m.get("role") == "system"]


def compute_frozen_hash(system_messages: list[dict]) -> str:
    """计算 system 消息列表的 SHA256 指纹。"""
    return hashlib.sha256(
        json.dumps(system_messages, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def detect_orphaned_system_msgs(
    current: list[dict], frozen: Optional[list[dict]]
) -> list[dict]:
    """检测当前 system 消息中哪些不在冻结集中（孤儿）。"""
    if frozen is None:
        return []
    frozen_set = {json.dumps(m, sort_keys=True, ensure_ascii=False) for m in frozen}
    return [
        {**m, "orphaned": True}
        for m in current
        if json.dumps(m, sort_keys=True, ensure_ascii=False) not in frozen_set
    ]


def validate_or_freeze_anchor(
    state: ProxyState,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    tools_msg: Optional[dict] = None,
) -> None:
    """验证或冻结锚点：首次出现稳定 system 消息后冻结，之后检测孤儿。"""
    system_msgs = extract_system_messages(messages)

    if not system_msgs:
        state.pre_freeze_turns = 0
        return

    if state.frozen_prefix_hash and state._anchor_from_non_system:
        state.log("[锚点] 检测到system消息，重置非system锚点")
        state.frozen_prefix_hash = None
        state.frozen_system_msgs = None
        state.pre_freeze_turns = 0
        state.frozen_tools_hash = None
        state.frozen_tools_count = 0
        state._frozen_tools_msg = None

    state._anchor_from_non_system = False

    current_hash = compute_frozen_hash(system_msgs)

    if state.frozen_prefix_hash is None:
        state.pre_freeze_turns += 1
        if state.pre_freeze_turns < PRE_FREEZE_DELAY:
            state.frozen_system_msgs = None
            return
        state.frozen_prefix_hash = current_hash
        state.frozen_system_msgs = [dict(m) for m in system_msgs]
        state.frozen_message_count = len(system_msgs)
        total_chars = sum(len(json.dumps(m, ensure_ascii=False)) for m in system_msgs)
        state.frozen_token_count = max(1, total_chars // 3)
        state.orphaned_system_msgs = []

        if tools and tools_msg:
            state.frozen_tools_hash = hashlib.sha256(
                json.dumps(tools, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            state.frozen_tools_count = len(tools)
            state._frozen_tools_msg = dict(tools_msg)
            state.log(f"[工具] {len(tools)}个工具定义已锁入锚定块")

        src = "非system" if state._anchor_from_non_system else "system"
        state.log(
            f"[锚点] 冻结({src}) — {len(system_msgs)}条, ~{state.frozen_token_count}tokens, {current_hash[:12]}..."
        )
        return

    orphaned = detect_orphaned_system_msgs(system_msgs, state.frozen_system_msgs)
    state.orphaned_system_msgs = orphaned
    if orphaned:
        state.log(
            f"[锚点] {len(orphaned)}条新system → 瞬态 (锚点{state.frozen_message_count}条未动)"
        )


# ============================================================
# 消息重组引擎
# ============================================================

def _is_skill_call(tool_call: dict) -> bool:
    """判断 tool_call 是否为技能调用。"""
    fn = tool_call.get("function", {})
    return fn.get("name") in ("skill_view", "skill_manage")


def _build_tools_message(tools: list[dict]) -> Optional[dict]:
    """v2.6.4: 将 tools 数组转为紧凑文本消息，锁入锚定块。"""
    if not tools:
        return None
    lines: list[str] = []
    for t in tools:
        fn = t.get("function", {})
        name = fn.get("name", "?")
        desc = fn.get("description", "")
        short_desc = desc[:80].replace("\n", " ").strip()
        lines.append(f"- {name}: {short_desc}")
    text = f"[工具定义] 已锁定{len(tools)}个工具:\n" + "\n".join(lines)
    return {"role": "system", "content": text, "frozen_tools": True}


def _extract_skill_name(
    tool_msg: dict, conversation: list[dict], tc_id: str
) -> str:
    """从对话中提取技能名称。"""
    try:
        fn = tool_msg.get("function", tool_msg.get("name", ""))
        if not fn:
            for cm in conversation:
                if cm.get("role") == "assistant" and cm.get("tool_calls"):
                    for tc in cm.get("tool_calls", []):
                        if tc["id"] == tc_id:
                            args = tc.get("function", {}).get("arguments", "{}")
                            try:
                                return json.loads(args).get("name", "unknown")
                            except Exception:
                                return args[:40]
    except Exception:
        pass
    return "unknown"


def rebuild_messages(state: ProxyState, messages: list[dict]) -> list[dict]:
    """重组消息：系统锚定块 + 对话块 + 瞬态块。"""
    if state.frozen_system_msgs is None:
        return messages

    system_msgs = list(state.frozen_system_msgs)
    frozen_fingerprints = {
        json.dumps(m, sort_keys=True, ensure_ascii=False) for m in system_msgs
    }
    conversation = [
        m
        for m in messages
        if json.dumps(
            {
                k: v
                for k, v in m.items()
                if k
                not in (
                    "ephemeral",
                    "orphaned",
                    "source_round",
                    "compressed_from_rounds",
                    "checkpoint",
                )
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        not in frozen_fingerprints
    ]

    all_tool_ids: set[str] = set()
    skill_call_ids: set[str] = set()
    for m in conversation:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                tid = tc["id"]
                all_tool_ids.add(tid)
                if _is_skill_call(tc):
                    skill_call_ids.add(tid)

    last_user_idx = -1
    for i in range(len(conversation) - 1, -1, -1):
        if conversation[i].get("role") == "user":
            last_user_idx = i
            break

    tool_loads: list[dict] = []
    clean_conversation: list[dict] = []

    for i, m in enumerate(conversation):
        role = m.get("role")

        if role == "tool":
            tid = m.get("tool_call_id")
            if tid and tid in all_tool_ids:
                is_skill = tid in skill_call_ids
                consumed_set = (
                    state._consumed_skill_ids
                    if is_skill
                    else state._consumed_tool_ids
                )

                if is_skill:
                    skill_name = _extract_skill_name(m, conversation, tid)
                    m_tiny = dict(m)
                    m_tiny["content"] = f"[技能已加载: {skill_name}]"
                    tool_loads.append(m_tiny)
                    if skill_name and skill_name != "unknown":
                        state._loaded_skill_names.add(skill_name)
                else:
                    # v2.8: 大体积工具输出截断（留头留尾）
                    MAX_TOOL_CHARS = 500
                    HEAD_CHARS = 150
                    m_out = dict(m)
                    raw = m_out.get("content", "")
                    if len(raw) > MAX_TOOL_CHARS:
                        tail_chars = MAX_TOOL_CHARS - HEAD_CHARS
                        m_out["content"] = (
                            raw[:HEAD_CHARS]
                            + f"\n... (中间{len(raw)-MAX_TOOL_CHARS}字符省略) ...\n"
                            + raw[-tail_chars:]
                        )
                    clean_conversation.append(m_out)
                consumed_set.add(tid)
                continue

        if role == "assistant" and m.get("tool_calls"):
            tcs = m.get("tool_calls", [])
            tc_ids = [tc["id"] for tc in tcs]

            for tid in tc_ids:
                if tid in skill_call_ids:
                    state._consumed_skill_ids.add(tid)
                else:
                    state._consumed_tool_ids.add(tid)

            has_results = all(
                any(
                    tm.get("role") == "tool" and tm.get("tool_call_id") == tid
                    for tm in conversation[i + 1 :]
                )
                for tid in tc_ids
            ) if tc_ids else False

            if has_results:
                clean_conversation.append(m)
            else:
                tool_loads.append(dict(m))
            continue

        clean_conversation.append(m)

    if tool_loads:
        skill_count = sum(
            1 for tl in tool_loads if "[技能已加载" in str(tl.get("content", ""))
        )
        tool_count = len(tool_loads) - skill_count
        parts: list[str] = []
        if skill_count:
            parts.append(f"{skill_count}条技能")
        if tool_count:
            parts.append(f"{tool_count}条工具")
        state.log(f"[重组] 抽出 {'+'.join(parts)} → 瞬态")

    if state._cut_before_turn is not None and state._cut_summary:
        KEEP_RECENT_USERS = 5
        user_indices: list[int] = []
        for i in range(len(clean_conversation) - 1, -1, -1):
            if clean_conversation[i].get("role") == "user":
                user_indices.append(i)
        user_indices.reverse()
        if len(user_indices) > KEEP_RECENT_USERS:
            cut_idx = user_indices[-KEEP_RECENT_USERS]
            old_part = clean_conversation[:cut_idx]
            old_users = [
                m.get("content", "")[:60]
                for m in old_part
                if m.get("role") == "user"
            ]
            old_user_summary = "；".join(old_users[-12:]) if old_users else "(无内容)"
            clean_conversation = clean_conversation[cut_idx:]
            clean_conversation.insert(
                0,
                {
                    "role": "system",
                    "content": f"[历史摘要] 此前{len(old_users)}轮要点: {old_user_summary}",
                    "checkpoint": True,
                },
            )
            state.log(
                f"[截断] 对话块砍至{len(clean_conversation)}条 (保留{KEEP_RECENT_USERS}轮, 摘要{len(old_users)}轮)"
            )
            state._cut_before_turn = None
            state._cut_summary = ""

    ephemeral: list[dict] = (
        list(state.orphaned_system_msgs)
        + list(state.ephemeral_buffer)
        + tool_loads
    )
    state._last_ephemeral_tokens = (
        sum(len(json.dumps(m, ensure_ascii=False)) for m in ephemeral) // 3
    )
    state._last_total_tokens = (
        sum(
            len(json.dumps(m, ensure_ascii=False))
            for m in system_msgs + clean_conversation + ephemeral
        )
        // 3
    )

    # v2.8.1: 在对话块插入已加载技能清单，避免模型重复加载
    if state._loaded_skill_names:
        skill_list = "、".join(sorted(state._loaded_skill_names))
        clean_conversation.insert(0, {
            "role": "system",
            "content": f"[已加载技能: {skill_list}] 以下技能已在本次会话中加载过，无需重复加载。",
            "ephemeral": False,
        })

    return system_msgs + clean_conversation + ephemeral


# ============================================================
# 自动检查点
# ============================================================

def _try_auto_checkpoint(state: ProxyState) -> None:
    """对话块超过阈值时自动冻结旧块。"""
    if state.frozen_system_msgs is None:
        return
    if state.total_turns - state._last_auto_checkpoint < 20:
        return

    frozen_blocks = state.frozen_token_count // 2048
    stable_idx = state._stable_indexed_blocks
    dialogue_blocks = stable_idx - frozen_blocks

    if dialogue_blocks >= AUTO_CHECKPOINT_BLOCKS:
        cut_blocks = int(dialogue_blocks * (1 - AUTO_CHECKPOINT_KEEP))
        state.inc_checkpoint()
        state._cut_before_turn = state.total_turns
        state._cut_summary = (
            f"[自动检查点#{state.checkpoint_count}-第{state.total_turns}轮] "
            f"对话块已达{dialogue_blocks}块，冻结前{cut_blocks}块。"
        )
        state._last_auto_checkpoint = state.total_turns
        state.log(
            f"[检查点] 自动#{state.checkpoint_count} — {dialogue_blocks}块≥{AUTO_CHECKPOINT_BLOCKS}，真砍{cut_blocks}块"
        )


# ============================================================
# 自动压缩瞬态块
# ============================================================

def _try_auto_compress(state: ProxyState) -> None:
    """瞬态块 token 数超阈值时自动压缩。"""
    if state._last_ephemeral_tokens <= EPHEMERAL_COMPRESS_THRESHOLD:
        return
    if state.ephemeral_compressed:
        pass
    old = state._last_ephemeral_tokens
    state.ephemeral_compressed = True
    state.ephemeral_buffer = [
        {
            "role": "system",
            "content": (
                f"[自动压缩-第{state.total_turns}轮] "
                f"工具结果已合并，预填控制在{EPHEMERAL_COMPRESS_TARGET}t以内"
            ),
            "ephemeral": True,
        }
    ]
    state._last_ephemeral_tokens = EPHEMERAL_COMPRESS_TARGET
    state.log(
        f"[自动压缩] 瞬态{old}t→{EPHEMERAL_COMPRESS_TARGET}t (阈值{EPHEMERAL_COMPRESS_THRESHOLD}t)"
    )


# ============================================================
# 缓存健康度
# ============================================================

def _update_stable_blocks(state: ProxyState) -> None:
    """从 OMLX 管理接口读取缓存块统计。"""
    try:
        with httpx.Client(timeout=ADMIN_TIMEOUT) as c:
            r = c.get("http://localhost:8001/admin/api/stats", headers=OMLX_HEADERS)
            stats = r.json()
            runtime_models = stats["runtime_cache"]["models"]
            active_ids = {
                am["id"] for am in stats.get("active_models", {}).get("models", [])
            }
            m_found = next(
                (m for m in runtime_models if m["id"] in active_ids), None
            )
            if m_found is None and runtime_models:
                m_found = runtime_models[0]
            if m_found:
                state._stable_hot_blocks = m_found.get("hot_cache_entries", 0)
                state._stable_indexed_blocks = m_found.get("indexed_blocks", 0)
    except Exception as e:
        if not getattr(state, "_stable_api_failed", False):
            state.log(f"[稳定块] OMLX管理API不可达: {e}")
            state._stable_api_failed = True
    else:
        state._stable_api_failed = False


def update_cache_health(
    state: ProxyState, cached_tokens: Optional[int], prompt_tokens: int
) -> None:
    """更新缓存健康度指标。"""
    state.last_prompt_tokens = prompt_tokens
    state.last_cached_tokens = cached_tokens or 0
    state.request_history.append(
        {
            "turn": state.total_turns + 1,
            "prompt": prompt_tokens,
            "cached": cached_tokens or 0,
            "net": prompt_tokens - (cached_tokens or 0),
            "time": time.strftime("%H:%M:%S"),
        }
    )
    if len(state.request_history) > 20:
        state.request_history = state.request_history[-20:]

    if cached_tokens is not None:
        hit_rate = min(1.0, cached_tokens / max(prompt_tokens, 1))
        state.stream_miss_count = 0
        if state.fallback_mode:
            state.fallback_mode = False
    else:
        state.stream_miss_count += 1
        if state.stream_miss_count >= STREAM_MISS_THRESHOLD:
            state.fallback_mode = True
        # v2.8: 无缓存数据时不生成虚假命中率，保留上次值
        _update_stable_blocks(state)
        _try_auto_checkpoint(state)
        _try_auto_compress(state)
        return

    state.hit_rate_window.append(hit_rate)
    if len(state.hit_rate_window) > WINDOW_SIZE:
        state.hit_rate_window = state.hit_rate_window[-WINDOW_SIZE:]

    wa = sum(state.hit_rate_window) / len(state.hit_rate_window)
    old = state.cache_state
    if wa >= HIT_RATE_HEALTHY:
        state.cache_state = "HEALTHY"
    elif wa >= HIT_RATE_DEGRADED:
        state.cache_state = "DEGRADED"
    elif wa >= HIT_RATE_CRITICAL:
        state.cache_state = "DEGRADED"
    else:
        state.cache_state = "CRITICAL"

    if old != state.cache_state:
        state.log(f"[缓存] {old} → {state.cache_state} (窗均={wa:.1%})")

    _update_stable_blocks(state)
    _try_auto_checkpoint(state)
    _try_auto_compress(state)


# ============================================================
# SSE 解析
# ============================================================

def parse_usage_from_sse(chunks: list[bytes]) -> Optional[dict]:
    """从 SSE 字节流中提取 usage 信息。"""
    full = b"".join(chunks).decode("utf-8", errors="replace")
    blocks = re.findall(r'data:\s*(\{.*?"usage".*?\})\s*\n', full, re.DOTALL)
    if not blocks:
        blocks = re.findall(r'data:\s*(\{.*?\})\s*\n', full, re.DOTALL)
        for b in reversed(blocks):
            try:
                d = json.loads(b)
                if "usage" in d:
                    return d["usage"]
            except Exception:
                continue
        return None
    try:
        return json.loads(blocks[-1]).get("usage")
    except Exception:
        return None


def extract_cached_tokens(usage: Optional[dict]) -> Optional[int]:
    """从 usage 中提取 cached_tokens。"""
    if not usage:
        return None
    return usage.get("prompt_tokens_details", {}).get("cached_tokens")
