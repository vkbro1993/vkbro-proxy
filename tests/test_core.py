"""
Vkbro-MLX Agent Cache Proxy v2.7.0 — 核心逻辑测试
================================================
使用 pytest 测试锚点引擎和消息重组引擎。
"""

import os
import sys

import pytest

# 支持 `python -m pytest tests/` 和 `pytest` 两种运行方式
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from core import (  # noqa: E402
    _build_tools_message,
    _extract_skill_name,
    _is_skill_call,
    compute_frozen_hash,
    detect_orphaned_system_msgs,
    extract_cached_tokens,
    extract_system_messages,
    rebuild_messages,
    validate_or_freeze_anchor,
)
from proxy_state import ProxyState  # noqa: E402


# ── extract_system_messages ──────────────────────────────────────────

def test_extract_system_messages_basic():
    """正确提取 system 消息。"""
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "system", "content": "Additional instructions."},
    ]
    result = extract_system_messages(messages)
    assert len(result) == 2
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "system"


def test_extract_system_messages_empty():
    """无 system 消息时返回空列表。"""
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]
    result = extract_system_messages(messages)
    assert result == []


def test_extract_system_messages_no_messages():
    """空消息列表返回空列表。"""
    assert extract_system_messages([]) == []


# ── compute_frozen_hash ──────────────────────────────────────────────

def test_compute_frozen_hash_same_input_same_hash():
    """相同输入产生相同 hash。"""
    sys_msgs = [{"role": "system", "content": "You are helpful."}]
    h1 = compute_frozen_hash(sys_msgs)
    h2 = compute_frozen_hash(sys_msgs)
    assert h1 == h2


def test_compute_frozen_hash_different_input():
    """不同输入产生不同 hash。"""
    a = [{"role": "system", "content": "You are helpful."}]
    b = [{"role": "system", "content": "You are dangerous."}]
    assert compute_frozen_hash(a) != compute_frozen_hash(b)


def test_compute_frozen_hash_list_order_matters():
    """hash 依赖列表顺序（sort_keys 只排序字典键，不排序数组元素）。"""
    a = [{"role": "system", "content": "A"}, {"role": "system", "content": "B"}]
    b = [{"role": "system", "content": "B"}, {"role": "system", "content": "A"}]
    assert compute_frozen_hash(a) != compute_frozen_hash(b)


# ── detect_orphaned_system_msgs ──────────────────────────────────────

def test_detect_orphaned_system_msgs_none_frozen():
    """frozen 为 None 时返回空。"""
    current = [{"role": "system", "content": "Test"}]
    assert detect_orphaned_system_msgs(current, None) == []


def test_detect_orphaned_system_msgs_no_orphans():
    """无孤儿时返回空。"""
    frozen = [{"role": "system", "content": "A"}]
    current = [{"role": "system", "content": "A"}]
    assert detect_orphaned_system_msgs(current, frozen) == []


def test_detect_orphaned_system_msgs_with_orphans():
    """有新 system 消息时正确标记 orphaned=True。"""
    frozen = [{"role": "system", "content": "A"}]
    current = [
        {"role": "system", "content": "A"},
        {"role": "system", "content": "B"},
    ]
    result = detect_orphaned_system_msgs(current, frozen)
    assert len(result) == 1
    assert result[0]["orphaned"] is True
    assert result[0]["content"] == "B"


# ── validate_or_freeze_anchor ────────────────────────────────────────

def test_validate_or_freeze_anchor_first_call():
    """首次调用增加预热计数但不冻结（需 PRE_FREEZE_DELAY=2 轮）。"""
    state = ProxyState()
    messages = [{"role": "system", "content": "You are helpful."}]
    validate_or_freeze_anchor(state, messages)
    assert state.pre_freeze_turns == 1
    assert state.frozen_prefix_hash is None


def test_validate_or_freeze_anchor_freeze_on_second_call():
    """第二轮预热后冻结锚点。"""
    state = ProxyState()
    messages = [{"role": "system", "content": "You are helpful."}]
    # First call: pre_freeze_turns = 1
    validate_or_freeze_anchor(state, messages)
    # Second call: pre_freeze_turns = 2 >= PRE_FREEZE_DELAY, should freeze
    validate_or_freeze_anchor(state, messages)
    assert state.frozen_prefix_hash is not None
    assert state.frozen_system_msgs is not None
    assert len(state.frozen_system_msgs) == 1
    assert state.frozen_message_count == 1
    assert state.frozen_token_count > 0


def test_validate_or_freeze_anchor_no_system():
    """无 system 消息时重置预热。"""
    state = ProxyState()
    messages = [{"role": "user", "content": "Hello"}]
    validate_or_freeze_anchor(state, messages)
    assert state.pre_freeze_turns == 0


def test_validate_or_freeze_anchor_with_tools():
    """带工具定义时同时冻结工具。"""
    state = ProxyState()
    state.reset_anchor()  # 清除单例残留状态
    messages = [{"role": "system", "content": "You are helpful."}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search the web",
            },
        }
    ]
    tools_msg = _build_tools_message(tools)
    # Two calls to freeze
    validate_or_freeze_anchor(state, messages)
    validate_or_freeze_anchor(state, messages, tools, tools_msg)
    assert state.frozen_tools_hash is not None
    assert state.frozen_tools_count == 1
    assert state._frozen_tools_msg is not None


# ── _is_skill_call ───────────────────────────────────────────────────

def test_is_skill_call_skill_view():
    tc = {"function": {"name": "skill_view"}}
    assert _is_skill_call(tc) is True


def test_is_skill_call_skill_manage():
    tc = {"function": {"name": "skill_manage"}}
    assert _is_skill_call(tc) is True


def test_is_skill_call_non_skill():
    tc = {"function": {"name": "read_file"}}
    assert _is_skill_call(tc) is False


def test_is_skill_call_no_function():
    tc = {}
    assert _is_skill_call(tc) is False


# ── _build_tools_message ─────────────────────────────────────────────

def test_build_tools_message_empty():
    assert _build_tools_message([]) is None


def test_build_tools_message_single():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search for information",
            },
        }
    ]
    result = _build_tools_message(tools)
    assert result is not None
    assert result["role"] == "system"
    assert result["frozen_tools"] is True
    assert "search" in result["content"]
    assert "1个工具" in result["content"]


def test_build_tools_message_truncates_long_desc():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "X" * 200,
            },
        }
    ]
    result = _build_tools_message(tools)
    assert result is not None
    # Description should be truncated to ~80 chars
    desc_in_msg = result["content"].split(": ", 1)[1] if ": " in result["content"] else ""
    assert len(desc_in_msg) <= 85  # 80 chars + name prefix


# ── rebuild_messages ─────────────────────────────────────────────────

def test_rebuild_messages_no_frozen():
    """未冻结时原样返回。"""
    state = ProxyState()
    state.reset_anchor()  # 清除单例残留状态
    messages = [{"role": "user", "content": "Hello"}]
    result = rebuild_messages(state, messages)
    assert result == messages


def test_rebuild_messages_basic_conversation():
    """冻结后正确分离 system + 对话。"""
    state = ProxyState()
    # 先冻结 system 消息
    sys_msg = {"role": "system", "content": "You are helpful."}
    for _ in range(2):
        validate_or_freeze_anchor(state, [sys_msg])

    messages = [sys_msg, {"role": "user", "content": "Hello"}]
    result = rebuild_messages(state, messages)
    # 结果应包含冻结的 system + 对话中的 user
    assert len(result) >= 2
    assert result[0]["role"] == "system"
    assert any(m["role"] == "user" for m in result)


def test_rebuild_messages_no_system_in_input():
    """无 system 消息的对话正确处理。"""
    state = ProxyState()
    # 预冻结
    sys_msg = {"role": "system", "content": "You are helpful."}
    for _ in range(2):
        validate_or_freeze_anchor(state, [sys_msg])

    messages = [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]
    result = rebuild_messages(state, messages)
    # 冻结的 system 在开头，对话紧随其后
    assert result[0]["role"] == "system"
    assert any(m["content"] == "Hello" for m in result)


# ── _extract_skill_name ──────────────────────────────────────────────

def test_extract_skill_name_from_args():
    """从 tool_calls arguments 提取技能名。"""
    conversation = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "tc1",
                    "function": {
                        "name": "skill_view",
                        "arguments": '{"name": "my-skill"}',
                    },
                }
            ],
        }
    ]
    tool_msg = {"role": "tool", "tool_call_id": "tc1", "content": "Loaded"}
    name = _extract_skill_name(tool_msg, conversation, "tc1")
    assert name == "my-skill"


def test_extract_skill_name_not_found():
    """找不到时返回 unknown。"""
    conversation = []
    tool_msg = {"role": "tool", "tool_call_id": "unknown", "content": "x"}
    name = _extract_skill_name(tool_msg, conversation, "unknown")
    assert name == "unknown"


# ── extract_cached_tokens ────────────────────────────────────────────

def test_extract_cached_tokens_none():
    assert extract_cached_tokens(None) is None


def test_extract_cached_tokens_present():
    usage = {"prompt_tokens_details": {"cached_tokens": 42}}
    assert extract_cached_tokens(usage) == 42


def test_extract_cached_tokens_missing():
    usage = {"prompt_tokens": 100}
    assert extract_cached_tokens(usage) is None


# ── ProxyState ───────────────────────────────────────────────────────

def test_proxy_state_singleton():
    """ProxyState 是单例。"""
    a = ProxyState()
    b = ProxyState()
    assert a is b


def test_proxy_state_initial_values():
    """初始值正确。"""
    state = ProxyState()
    state.reset_anchor()  # 清除单例残留状态
    assert state.frozen_prefix_hash is None
    assert state.frozen_system_msgs is None
    assert state.cache_state == "HEALTHY"
    assert state.total_turns == 0
    assert state.ephemeral_compressed is False


def test_proxy_state_log():
    """log 方法记录到缓冲区。"""
    state = ProxyState()
    state.log("test message")
    logs = state.get_logs()
    assert any("test message" in entry for entry in logs)


def test_proxy_state_reset_anchor():
    """reset_anchor 清空所有锚点状态。"""
    state = ProxyState()
    state.frozen_prefix_hash = "abc123"
    state.frozen_system_msgs = [{"role": "system"}]
    state.reset_anchor()
    assert state.frozen_prefix_hash is None
    assert state.frozen_system_msgs is None
    assert state.frozen_tools_hash is None
    assert state._consumed_skill_ids == set()


def test_proxy_state_checkpoint_counter():
    """检查点计数器递增。"""
    state = ProxyState()
    assert state.checkpoint_count == 0
    c = state.inc_checkpoint()
    assert c == 1
    assert state.checkpoint_count == 1


def test_proxy_state_snapshot():
    """snapshot 返回只读快照。"""
    state = ProxyState()
    snap = state.snapshot()
    assert snap["cache_state"] == "HEALTHY"
    assert snap["total_turns"] == 0
    assert "window_avg_hit_rate" in snap
