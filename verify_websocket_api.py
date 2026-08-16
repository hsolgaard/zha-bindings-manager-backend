"""One-off verification script — not shipped, not part of the integration.

Imports the *real* websocket_api.py against minimal fake
`homeassistant.*` modules (there's no real Home Assistant installed in
this environment, and pulling in the full package plus
pytest-homeassistant-custom-component just to exercise three small async
functions isn't worth the weight). This is deliberately similar in spirit
to the card's own smoke-test.js: fake just enough of the host environment
to load the real module and call its real functions, not a reimplementation
of the logic under test.

Covers exactly the behavior with real risk of being wrong: get_capabilities
responding at all, get_state returning null vs. an existing entry, and
save_state's last-write-wins conflict check (accept when base_updated_at
matches what's stored, reject with "stale_write" when it doesn't) — the
one piece of real logic in this integration, per the delivery plan's
"whole-state, last-write-wins, no merge logic" scope.
"""
import asyncio
import sys
import types


# ---- Minimal fake homeassistant.* modules, just enough surface for
# ---- websocket_api.py to import and run for real. ----

ha = types.ModuleType("homeassistant")
ha_components = types.ModuleType("homeassistant.components")
ha_core = types.ModuleType("homeassistant.core")
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_websocket_api = types.ModuleType("homeassistant.components.websocket_api")
ha_storage = types.ModuleType("homeassistant.helpers.storage")


def _identity_decorator_factory(*_a, **_kw):
    def deco(fn):
        return fn
    return deco


ha_websocket_api.websocket_command = _identity_decorator_factory
ha_websocket_api.async_response = lambda fn: fn
ha_websocket_api.async_register_command = lambda hass, fn: None


class _FakeActiveConnection:
    """Captures whatever the real send_result/send_error calls pass it,
    so the test can assert on it — stands in for the real
    websocket_api.ActiveConnection."""

    def __init__(self):
        self.results = {}
        self.errors = {}

    def send_result(self, msg_id, result=None):
        self.results[msg_id] = result

    def send_error(self, msg_id, code, message):
        self.errors[msg_id] = (code, message)


ha_websocket_api.ActiveConnection = _FakeActiveConnection


class _FakeHomeAssistant:
    def __init__(self):
        self.data = {}


ha_core.HomeAssistant = _FakeHomeAssistant
ha_core.callback = lambda fn: fn


class _FakeStore:
    """In-memory stand-in for homeassistant.helpers.storage.Store — same
    async_load/async_save shape, backed by a plain dict instead of disk."""

    def __init__(self, hass, version, key):
        self.hass = hass
        self.version = version
        self.key = key
        self._data = None

    async def async_load(self):
        return self._data

    async def async_save(self, data):
        self._data = data


ha_storage.Store = _FakeStore

sys.modules["homeassistant"] = ha
sys.modules["homeassistant.components"] = ha_components
sys.modules["homeassistant.components.websocket_api"] = ha_websocket_api
sys.modules["homeassistant.core"] = ha_core
sys.modules["homeassistant.helpers"] = ha_helpers
sys.modules["homeassistant.helpers.storage"] = ha_storage

sys.path.insert(0, "custom_components")
import importlib

# Register zha_bindings_manager as an already-loaded (empty) package before
# importing its websocket_api submodule, so Python resolves the relative
# "from .const import ..." correctly without ever executing the real
# __init__.py (which pulls in homeassistant.config_entries — not worth
# stubbing that too just to reach a submodule import).
_pkg = types.ModuleType("zha_bindings_manager")
_pkg.__path__ = ["custom_components/zha_bindings_manager"]
sys.modules["zha_bindings_manager"] = _pkg

websocket_api = importlib.import_module("zha_bindings_manager.websocket_api")


async def main():
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))

    hass = _FakeHomeAssistant()

    # --- get_capabilities ---
    conn = _FakeActiveConnection()
    await websocket_api.websocket_get_capabilities(hass, conn, {"id": 1})
    check("get_capabilities responds with available=True", conn.results[1]["available"] is True)

    # --- get_state on an empty store ---
    conn = _FakeActiveConnection()
    await websocket_api.websocket_get_state(hass, conn, {"id": 2, "card_id": "default"})
    check("get_state returns None when nothing's been saved yet", conn.results[2] is None)

    # --- save_state: first save ever, base_updated_at absent/None -> accepted ---
    conn = _FakeActiveConnection()
    await websocket_api.websocket_save_state(
        hass, conn, {"id": 3, "card_id": "default", "state": {"hello": "world"}, "base_updated_at": None}
    )
    check("first save is accepted", 3 in conn.results and "updated_at" in conn.results[3])
    first_updated_at = conn.results[3]["updated_at"]

    # --- get_state now returns what was just saved ---
    conn = _FakeActiveConnection()
    await websocket_api.websocket_get_state(hass, conn, {"id": 4, "card_id": "default"})
    check(
        "get_state reflects the saved state and updated_at",
        conn.results[4]["state"] == {"hello": "world"} and conn.results[4]["updated_at"] == first_updated_at,
    )

    # --- save_state with a stale base_updated_at (simulates a second
    # browser that loaded before the first browser's save) -> rejected ---
    conn = _FakeActiveConnection()
    await websocket_api.websocket_save_state(
        hass,
        conn,
        {"id": 5, "card_id": "default", "state": {"from": "stale client"}, "base_updated_at": "some-old-timestamp"},
    )
    check(
        "a save with a stale base_updated_at is rejected as stale_write, not silently applied",
        conn.errors.get(5, (None, None))[0] == "stale_write",
    )

    # --- confirm the rejected save really didn't overwrite anything ---
    conn = _FakeActiveConnection()
    await websocket_api.websocket_get_state(hass, conn, {"id": 6, "card_id": "default"})
    check("the stale-rejected save left the stored state untouched", conn.results[6]["state"] == {"hello": "world"})

    # --- save_state with the *correct* current base_updated_at -> accepted ---
    conn = _FakeActiveConnection()
    await websocket_api.websocket_save_state(
        hass,
        conn,
        {"id": 7, "card_id": "default", "state": {"hello": "updated"}, "base_updated_at": first_updated_at},
    )
    check("a save with the correct base_updated_at is accepted", 7 in conn.results)
    second_updated_at = conn.results[7]["updated_at"]
    check("a successful save produces a new updated_at", second_updated_at != first_updated_at)

    # --- two different card_ids don't collide ---
    conn = _FakeActiveConnection()
    await websocket_api.websocket_save_state(
        hass, conn, {"id": 8, "card_id": "second-card", "state": {"separate": True}, "base_updated_at": None}
    )
    conn2 = _FakeActiveConnection()
    await websocket_api.websocket_get_state(hass, conn2, {"id": 9, "card_id": "default"})
    check(
        "saving a different card_id doesn't affect the first card's state",
        conn2.results[9]["state"] == {"hello": "updated"},
    )

    fails = 0
    for name, ok in checks:
        print(("ok  " if ok else "FAIL") + "  " + name)
        if not ok:
            fails += 1
    print("\nAll checks passed." if fails == 0 else f"\n{fails} check(s) failed.")
    return fails


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
