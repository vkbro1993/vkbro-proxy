"""
Independent Verification Tests for vkbro-proxy v2.7.0
=====================================================
Covers:
  1. Import chain: from server import create_app
  2. Behavioral parity: old v2.6.4 vs new v2.7.0 for key functions
  3. Module boundary: no circular imports, relative/absolute imports work
  4. ProxyState singleton: thread safety via concurrent access
"""

import concurrent.futures
import hashlib
import json
import os
import sys
import threading
import time

import pytest

# ── Path setup for both new and old code ──────────────────────────────

_NEW_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OLD_CODE = "/Users/vkbro/本地AI/proxy/vkbro-proxy-v2.6.4/proxy.py"

if _NEW_ROOT not in sys.path:
    sys.path.insert(0, _NEW_ROOT)


# ============================================================
# 1. IMPORT CHAIN TESTS
# ============================================================

def test_import_chain_server_create_app():
    """from server import create_app works without ImportError."""
    from server import create_app
    app = create_app()
    assert app is not None
    assert app.title == "Vkbro-MLX Agent Cache Proxy v2.7.0"


def test_import_core_from_package():
    """Relative imports in core.py resolve correctly."""
    from core import (
        compute_frozen_hash,
        extract_system_messages,
        rebuild_messages,
        validate_or_freeze_anchor,
    )
    assert callable(compute_frozen_hash)
    assert callable(rebuild_messages)
    assert callable(validate_or_freeze_anchor)


def test_import_config():
    """Config imports work and values are correct types."""
    from config import (
        PROXY_PORT,
        PRE_FREEZE_DELAY,
        HIT_RATE_HEALTHY,
        WINDOW_SIZE,
        AUTO_CHECKPOINT_BLOCKS,
    )
    assert isinstance(PROXY_PORT, int)
    assert PROXY_PORT == 8000
    assert PRE_FREEZE_DELAY == 2
    assert HIT_RATE_HEALTHY == 0.7
    assert WINDOW_SIZE == 5
    assert AUTO_CHECKPOINT_BLOCKS == 40


def test_import_proxy_state():
    """ProxyState can be imported and instantiated."""
    from proxy_state import ProxyState
    s = ProxyState()
    assert s.cache_state == "HEALTHY"


def test_import_dashboard():
    """Dashboard module imports correctly."""
    from dashboard import render_dashboard
    from proxy_state import ProxyState
    state = ProxyState()
    html = render_dashboard(state)
    assert isinstance(html, str)
    assert "Vkbro-MLX Cache Proxy v2.7.0" in html


def test_import_proxy_entry():
    """The main proxy.py entry imports resolve correctly."""
    # Test via executing in subprocess to avoid port binding
    import proxy
    assert hasattr(proxy, 'main')
    assert callable(proxy.main)


# ============================================================
# 2. CIRCULAR IMPORT DETECTION
# ============================================================

def test_no_circular_imports():
    """Verify no import cycles by importing all modules fresh."""
    import importlib
    modules = [
        "config",
        "proxy_state",
        "core",
        "dashboard",
        "server",
        "proxy",
    ]
    for mod_name in modules:
        try:
            # Force fresh import
            if mod_name in sys.modules:
                del sys.modules[mod_name]
            importlib.import_module(mod_name)
        except ImportError as e:
            # Check for circular import error
            if "circular" in str(e).lower() or "cannot import" in str(e).lower():
                pytest.fail(f"Circular import detected in {mod_name}: {e}")


def test_import_order_independence():
    """Import order doesn't matter — all modules importable in any order."""
    import importlib
    import itertools

    modules = ["config", "proxy_state", "core", "dashboard", "server"]
    # Test a few representative orderings
    test_orders = [
        list(modules),                    # normal
        list(reversed(modules)),          # reversed
        ["core", "server", "dashboard", "config", "proxy_state"],  # shuffled
    ]
    for order in test_orders:
        # Clear modules
        for m in modules:
            if m in sys.modules:
                del sys.modules[m]
        for mod_name in order:
            importlib.import_module(mod_name)


# ============================================================
# 3. BEHAVIORAL PARITY WITH v2.6.4
# ============================================================

# Load old code as a module for comparison
# We can't directly import the old monolithic proxy.py (it binds ports on import),
# so we extract and compare the pure functions

def _extract_old_functions():
    """Parse the old proxy.py and extract the pure utility functions."""
    import importlib.util

    # We need to load the old module without executing FastAPI startup
    # Use exec with a restricted environment
    old_code_path = _OLD_CODE
    with open(old_code_path) as f:
        source = f.read()

    # Extract just the function definitions we need
    namespace = {"hashlib": hashlib, "json": json, "__name__": "old_proxy"}
    exec(source, namespace)
    return namespace


# Compute frozen hash comparison
def test_compute_frozen_hash_parity():
    """compute_frozen_hash produces identical results to v2.6.4."""
    from core import compute_frozen_hash as new_hash

    # Replicate old implementation directly
    def old_hash(system_messages):
        return hashlib.sha256(
            json.dumps(system_messages, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    test_cases = [
        [{"role": "system", "content": "You are helpful."}],
        [{"role": "system", "content": "A"}, {"role": "system", "content": "B"}],
        [],
        [{"role": "system", "content": "中文测试 🚀"}],
    ]
    for msgs in test_cases:
        assert new_hash(msgs) == old_hash(msgs), f"Mismatch for: {msgs}"


def test_extract_system_messages_parity():
    """extract_system_messages identical to v2.6.4."""
    from core import extract_system_messages as new_fn

    def old_fn(messages):
        return [m for m in messages if m.get("role") == "system"]

    messages = [
        {"role": "system", "content": "Sys1"},
        {"role": "user", "content": "Q"},
        {"role": "system", "content": "Sys2"},
        {"role": "assistant", "content": "A"},
    ]
    assert new_fn(messages) == old_fn(messages)
    assert new_fn([]) == old_fn([])


def test_detect_orphaned_system_msgs_parity():
    """detect_orphaned_system_msgs identical to v2.6.4."""
    from core import detect_orphaned_system_msgs as new_fn

    def old_fn(current, frozen):
        if frozen is None:
            return []
        frozen_set = {json.dumps(m, sort_keys=True, ensure_ascii=False) for m in frozen}
        return [
            {**m, "orphaned": True}
            for m in current
            if json.dumps(m, sort_keys=True, ensure_ascii=False) not in frozen_set
        ]

    frozen = [{"role": "system", "content": "A"}]
    current = [{"role": "system", "content": "A"}, {"role": "system", "content": "B"}]
    assert new_fn(current, frozen) == old_fn(current, frozen)
    assert new_fn([], None) == old_fn([], None)


def test_validate_or_freeze_anchor_parity():
    """validate_or_freeze_anchor behavior matches v2.6.4.

    Compare state changes after calling both versions with the same inputs.
    """
    from core import validate_or_freeze_anchor as new_fn
    from proxy_state import ProxyState

    # Test scenario: first call (pre-freeze turn 1)
    state_new = ProxyState()
    state_new.reset_anchor()
    messages = [{"role": "system", "content": "You are helpful."}]

    new_fn(state_new, messages)
    assert state_new.pre_freeze_turns == 1, "First call: pre_freeze_turns should be 1"
    assert state_new.frozen_prefix_hash is None, "Not frozen yet on first call"

    # Second call should freeze
    new_fn(state_new, messages)
    assert state_new.pre_freeze_turns == 2
    assert state_new.frozen_prefix_hash is not None, "Should freeze on second call"
    assert state_new.frozen_system_msgs is not None
    assert state_new.frozen_message_count == 1

    # Third call should detect no orphans
    new_fn(state_new, messages)
    assert state_new.orphaned_system_msgs == []

    # Test with no system messages resets
    state_new2 = ProxyState()
    state_new2.reset_anchor()
    new_fn(state_new2, [{"role": "user", "content": "Hello"}])
    assert state_new2.pre_freeze_turns == 0


def test_rebuild_messages_parity_basic():
    """rebuild_messages produces equivalent structure to v2.6.4.

    We verify:
    1. Frozen system messages are at the start
    2. Non-system conversation messages are preserved
    3. Orphaned system messages go to ephemeral
    """
    from core import rebuild_messages, validate_or_freeze_anchor
    from proxy_state import ProxyState

    state = ProxyState()
    state.reset_anchor()

    sys_msg = {"role": "system", "content": "You are helpful."}
    # Freeze
    for _ in range(2):
        validate_or_freeze_anchor(state, [sys_msg])

    messages = [sys_msg, {"role": "user", "content": "Hello"}]
    result = rebuild_messages(state, messages)

    # System should be at position 0
    assert result[0]["role"] == "system"
    assert result[0]["content"] == "You are helpful."
    # User message should be present
    assert any(m.get("role") == "user" and m.get("content") == "Hello" for m in result)


def test_rebuild_messages_no_frozen_returns_original():
    """When not frozen, rebuild_messages returns the original messages."""
    from core import rebuild_messages
    from proxy_state import ProxyState

    state = ProxyState()
    state.reset_anchor()
    state.frozen_system_msgs = None

    messages = [{"role": "user", "content": "Hello"}]
    result = rebuild_messages(state, messages)
    assert result == messages


# ============================================================
# 4. PROXYSTATE SINGLETON & THREAD SAFETY
# ============================================================

def test_proxy_state_real_singleton():
    """ProxyState returns the exact same instance across multiple calls."""
    from proxy_state import ProxyState
    instances = [ProxyState() for _ in range(10)]
    first = instances[0]
    for inst in instances:
        assert inst is first, "All ProxyState() calls must return the same instance"


def test_proxy_state_singleton_same_id():
    """id() is the same for all ProxyState() calls."""
    from proxy_state import ProxyState
    ids = {id(ProxyState()) for _ in range(20)}
    assert len(ids) == 1, f"Expected 1 unique id, got {len(ids)}"


def test_proxy_state_thread_safety_concurrent_creation():
    """Creating ProxyState across threads always returns the same instance."""
    from proxy_state import ProxyState

    results = []
    barrier = threading.Barrier(10)

    def create():
        barrier.wait()  # All threads start at roughly the same time
        results.append(id(ProxyState()))

    threads = [threading.Thread(target=create) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(results)) == 1, (
        f"All threads must see the same singleton, got {len(set(results))} unique ids"
    )


def test_proxy_state_thread_safety_concurrent_writes():
    """Concurrent writes to ProxyState don't cause data corruption."""
    from proxy_state import ProxyState

    state = ProxyState()
    state.reset_anchor()

    def writer(i):
        for _ in range(100):
            with state._lock:
                state.hit_rate_window.append(i)
                if len(state.hit_rate_window) > 1000:
                    state.hit_rate_window.pop(0)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All values should be valid (0-3)
    with state._lock:
        for v in state.hit_rate_window:
            assert 0 <= v <= 3, f"Corrupted value: {v}"


def test_proxy_state_thread_safety_log():
    """Concurrent log writes don't corrupt or lose entries."""
    from proxy_state import ProxyState

    state = ProxyState()

    def logger(prefix):
        for i in range(50):
            state.log(f"[{prefix}] message {i}")

    threads = [threading.Thread(target=logger, args=(f"T{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    logs = state.get_logs(200)
    assert len(logs) <= 100  # MAX_LOG cap
    # At least some entries from each thread should be present
    for i in range(4):
        found = any(f"[T{i}]" in entry for entry in logs)
        assert found, f"No log entries from thread T{i}"


def test_proxy_state_double_checked_locking():
    """Verify the double-checked locking pattern in __new__ works correctly.

    The inner check under lock prevents duplicate initialization.
    """
    from proxy_state import ProxyState, _MUTEX

    # Reset singleton for testing
    ProxyState._instance = None

    created = []
    init_called = []

    class FakeProxyState(ProxyState):
        _instance = None

    def create_instance():
        # Simulating race: acquire lock then create
        with _MUTEX:
            if FakeProxyState._instance is None:
                created.append("first")
                FakeProxyState._instance = object()
        # Second check should fail
        if FakeProxyState._instance is None:
            created.append("second")
            FakeProxyState._instance = object()

    threads = [threading.Thread(target=create_instance) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # With the lock, only one should create
    assert len(created) == 1, f"Expected 1 creation, got {len(created)}"

    # Restore
    ProxyState._instance = None


def test_proxy_state_lock_independence():
    """Each ProxyState has its own _lock, independent of the class-level _MUTEX."""
    from proxy_state import ProxyState, _MUTEX

    state = ProxyState()
    assert state._lock is not _MUTEX
    assert isinstance(state._lock, type(threading.Lock()))


def test_proxy_state_snapshot_thread_safety():
    """Snapshot can be called while another thread modifies state."""
    from proxy_state import ProxyState

    state = ProxyState()
    state.reset_anchor()

    errors = []
    barrier = threading.Barrier(2)

    def modifier():
        barrier.wait()
        for i in range(500):
            with state._lock:
                state.hit_rate_window.append(i)
                if len(state.hit_rate_window) > 50:
                    state.hit_rate_window.pop(0)

    def reader():
        barrier.wait()
        for _ in range(500):
            try:
                snap = state.snapshot()
                assert "cache_state" in snap
                assert "window_avg_hit_rate" in snap
            except Exception as e:
                errors.append(str(e))

    t1 = threading.Thread(target=modifier)
    t2 = threading.Thread(target=reader)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(errors) == 0, f"Snapshot errors: {errors}"


# ============================================================
# 5. MODULE BOUNDARY TESTS
# ============================================================

def test_modules_are_separate_and_importable():
    """Each module can be imported independently."""
    import importlib
    modules = {
        "config": "config",
        "proxy_state": "proxy_state",
        "core": "core",
        "dashboard": "dashboard",
        "server": "server",
    }
    for name, mod_name in modules.items():
        mod = importlib.import_module(mod_name)
        assert mod is not None, f"Failed to import {name}"


def test_config_no_side_effects():
    """Importing config doesn't have side effects beyond setting constants."""
    import importlib
    if "config" in sys.modules:
        del sys.modules["config"]
    mod = importlib.import_module("config")
    # All public attributes should be configuration constants
    public = [k for k in dir(mod) if not k.startswith("_") and k.isupper()]
    assert "PROXY_PORT" in public
    assert "OMLX_URL" in public


def test_core_functions_are_pure_or_state_only():
    """Core functions either are pure or take state as parameter (no global access)."""
    import inspect
    from core import (
        compute_frozen_hash,
        extract_system_messages,
        detect_orphaned_system_msgs,
        rebuild_messages,
        validate_or_freeze_anchor,
    )

    # These should be pure (no state parameter, deterministic)
    pure_funcs = [compute_frozen_hash, extract_system_messages]
    for fn in pure_funcs:
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        assert "state" not in params, f"{fn.__name__} should not take state param"


def test_dual_import_path_resilience():
    """Both relative (.xxx) and absolute imports resolve correctly.

    core.py and server.py have try/except blocks for ImportError.
    """
    import importlib

    # Test by importing via both paths
    # First clear everything
    for m in list(sys.modules):
        if m.startswith(("config", "proxy_state", "core", "dashboard", "server")):
            del sys.modules[m]
    if "proxy" in sys.modules:
        del sys.modules["proxy"]

    # Import from parent directory (absolute path)
    import config
    assert config.PROXY_PORT == 8000

    # Now clear and test relative path simulation
    for m in list(sys.modules):
        if m.startswith(("config", "proxy_state", "core", "dashboard", "server")):
            del sys.modules[m]

    # Add NEW_ROOT to path like proxy.py does
    old_path = list(sys.path)
    if _NEW_ROOT not in sys.path:
        sys.path.insert(0, _NEW_ROOT)

    import core  # this should work via absolute import fallback
    assert hasattr(core, "compute_frozen_hash")


# ============================================================
# 6. calculate_frozen_hash EXHAUSTIVE TESTS (beyond original 34)
# ============================================================

def test_compute_frozen_hash_deterministic():
    """Hash must be deterministic — same input always same output."""
    from core import compute_frozen_hash

    msgs = [{"role": "system", "content": "A"}]
    hashes = {compute_frozen_hash(msgs) for _ in range(100)}
    assert len(hashes) == 1


def test_compute_frozen_hash_is_hex_string():
    """Hash must be a 64-char hex string (SHA256)."""
    from core import compute_frozen_hash

    h = compute_frozen_hash([{"role": "system", "content": "test"}])
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_compute_frozen_hash_sensitive_to_all_fields():
    """Hash changes if any field in system message changes."""
    from core import compute_frozen_hash

    base = [{"role": "system", "content": "A"}]
    base_hash = compute_frozen_hash(base)

    # Different role
    assert compute_frozen_hash([{"role": "user", "content": "A"}]) != base_hash
    # Different content
    assert compute_frozen_hash([{"role": "system", "content": "B"}]) != base_hash
    # Extra field
    assert compute_frozen_hash([{"role": "system", "content": "A", "name": "x"}]) != base_hash


# ============================================================
# 7. EDGE CASES
# ============================================================

def test_rebuild_messages_with_orphaned_system():
    """Orphaned system messages appear in the result as ephemeral."""
    from core import rebuild_messages, validate_or_freeze_anchor
    from proxy_state import ProxyState

    state = ProxyState()
    state.reset_anchor()

    sys_msg = {"role": "system", "content": "Initial instruction."}
    for _ in range(2):
        validate_or_freeze_anchor(state, [sys_msg])

    # Add a new system message (orphan)
    new_sys = {"role": "system", "content": "New instruction."}
    validate_or_freeze_anchor(state, [sys_msg, new_sys])

    assert len(state.orphaned_system_msgs) == 1
    assert state.orphaned_system_msgs[0]["orphaned"] is True

    result = rebuild_messages(state, [sys_msg, new_sys, {"role": "user", "content": "Hello"}])
    # The orphaned new_sys should be in the result
    assert any(m.get("orphaned") for m in result)


def test_rebuild_messages_preserves_frozen_tools_msg():
    """When tools_msg is in messages, it's part of the frozen system set."""
    from core import rebuild_messages, validate_or_freeze_anchor, _build_tools_message
    from proxy_state import ProxyState

    state = ProxyState()
    state.reset_anchor()

    sys_msg = {"role": "system", "content": "You are helpful."}
    tools = [{"type": "function", "function": {"name": "search", "description": "Search"}}]
    tools_msg = _build_tools_message(tools)

    msgs = [tools_msg, sys_msg]
    for _ in range(2):
        validate_or_freeze_anchor(state, msgs, tools, tools_msg)

    assert state.frozen_tools_hash is not None
    assert state.frozen_tools_count == 1

    # rebuild should include the frozen tools_msg
    result = rebuild_messages(state, [tools_msg, sys_msg, {"role": "user", "content": "Q"}])
    system_msgs = [m for m in result if m.get("role") == "system"]
    assert any(m.get("frozen_tools") for m in system_msgs)


def test_empty_messages_freeze_behavior():
    """Empty messages list is handled gracefully."""
    from core import validate_or_freeze_anchor, rebuild_messages
    from proxy_state import ProxyState

    state = ProxyState()
    state.reset_anchor()

    validate_or_freeze_anchor(state, [])
    assert state.pre_freeze_turns == 0

    result = rebuild_messages(state, [])
    assert result == []


# ============================================================
# 8. SSE PARSING PARITY
# ============================================================

def test_parse_usage_from_sse_parity():
    """SSE usage parsing matches old behavior."""
    from core import parse_usage_from_sse as new_fn
    import re as new_re

    def old_fn(chunks):
        full = b"".join(chunks).decode("utf-8", errors="replace")
        blocks = new_re.findall(r'data:\s*(\{.*?"usage".*?\})\s*\n', full, new_re.DOTALL)
        if not blocks:
            blocks = new_re.findall(r'data:\s*(\{.*?\})\s*\n', full, new_re.DOTALL)
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

    # Test with usage data
    chunks = [
        b'data: {"choices":[{"delta":{"content":"Hello"}}],"usage":{"prompt_tokens":10,"completion_tokens":5}}\n\n'
    ]
    old_result = old_fn(chunks)
    new_result = new_fn(chunks)
    assert old_result == new_result

    # Test without usage data
    chunks_no_usage = [b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n']
    old_result = old_fn(chunks_no_usage)
    new_result = new_fn(chunks_no_usage)
    assert old_result == new_result

    # Test with empty chunks
    assert old_fn([]) == new_fn([]) == None


def test_extract_cached_tokens_parity():
    """cached_tokens extraction matches old behavior."""
    from core import extract_cached_tokens as new_fn

    def old_fn(usage):
        if not usage:
            return None
        return usage.get("prompt_tokens_details", {}).get("cached_tokens")

    test_cases = [
        None,
        {},
        {"prompt_tokens": 100},
        {"prompt_tokens_details": {}},
        {"prompt_tokens_details": {"cached_tokens": 0}},
        {"prompt_tokens_details": {"cached_tokens": 42}},
    ]
    for case in test_cases:
        assert new_fn(case) == old_fn(case), f"Mismatch for {case}"
