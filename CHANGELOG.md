# Changelog

## v2.7.0 (2026-06-15)

### Modular Rewrite
- Split 1045-line single file into 6 modules: `config.py`, `proxy_state.py`, `core.py`, `server.py`, `dashboard.py`, `proxy.py`
- `ProxyState` class with `threading.Lock` replaces global `_state` dict — thread-safe singleton
- External config via `.env` (`python-dotenv`)
- Dashboard HTML extracted to dedicated module

### Quality
- 68 test cases (unit + verification), 100% pass rate
- `create_app()` factory function for clean FastAPI instantiation
- Dual import paths (relative + absolute) for flexible deployment

### Bug Fixes
- `_handle_stream`: replaced full-response accumulation with 8KB tail buffer (O(1) memory)
- `_update_stable_blocks`: exceptions now logged instead of silently swallowed
- Removed dead `is_current_turn` variable in `rebuild_messages`

### Documentation
- English README with Custom Agent Integration guide
- Added `LICENSE` (MIT), `requirements.txt`, `.gitignore`, `CHANGELOG.md`

---

## v2.6.4 (2026-06-14)

- Auto model detection — queries OMLX for current model instead of hardcoding
- Proxy block map (anchor green / dialogue orange / ephemeral gray)
- Dashboard condensed to 4 overview cards + proxy vs OMLX comparison
- Control buttons: freeze old dialogue, restart OMLX

---

## v2.6.3 (2026-06-14)

- Real truncation: keep only last 5 turns, summarize old user messages
- Summary placed permanently at start of dialogue block

---

## v2.6.2 (2026-06-14)

- Anchor fix: don't freeze when no system messages present
- Tool result repositioning: results → dialogue block (KV cached), calls → ephemeral
- Fixed tool result stacking causing model repetition
- Dashboard model matching by active OMLX model

---

## v2.6.1 (2026-06-14)

- Ephemeral block fix: only current-turn tool interactions in transient
- Fixed 39-item ephemeral accumulation causing model loop

---

## v2.6 (2026-06-12)

- Initial three-block architecture: anchor + dialogue + ephemeral
- Tool definitions locked into anchor block
- Auto-freeze after 2-turn warmup
- Auto-checkpoint at 40-block threshold
- Auto-compress ephemeral at 4000 token threshold
- Cache health monitoring
- Dashboard WebUI
