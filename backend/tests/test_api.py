"""
Behavioural tests through the real HTTP + WebSocket routes (SPEC §17).

These assert observable behaviour — the registry actually lists widgets, a
state_intents widget actually emits state, the tab state-machine actually
applies peek-revert, a fired trigger actually reaches the system channel — not
just that modules import.
"""

from __future__ import annotations

import json

import pytest


# --- registry / picker ----------------------------------------------------

def test_builtin_widgets_register(client):
    ids = {w["id"] for w in client.get("/api/widgets").json()}
    assert {"clock", "shell-command", "system-stats", "mixer",
            "window-inventory"}.issubset(ids)


def test_terminal_unavailable_without_ttyd(client):
    widgets = {w["id"]: w for w in client.get("/api/widgets").json()}
    term = widgets["terminal"]
    if not term["available"]:
        assert "ttyd" in term["unavailable_reason"]


# --- tab state machine (SPEC §6) ------------------------------------------

def test_tab_create_select_and_peek_revert(client):
    client.post("/api/tabs/work/create")
    client.post("/api/tabs/music/create")

    # Selecting a disabled tab is a peek; switching away reverts it to disabled.
    client.post("/api/tabs/work/select")
    client.post("/api/tabs/music/select")
    states = {t["name"]: t["state"] for t in client.get("/api/tabs").json()["tabs"]}
    assert states["music"] == "selected"
    assert states["work"] == "disabled"   # peek reverted

    # An enabled tab stays enabled when you switch away.
    client.post("/api/tabs/work/enable")
    client.post("/api/tabs/music/select")
    states = {t["name"]: t["state"] for t in client.get("/api/tabs").json()["tabs"]}
    assert states["work"] == "enabled"


def test_tab_rename_duplicate_delete_reorder(client):
    client.post("/api/tabs/alpha/create")
    client.post("/api/tabs/alpha/select")
    client.post("/api/instances", json={"widget_id": "clock"})

    dup = client.post("/api/tabs/alpha/duplicate").json()["new"]
    assert dup == "alpha copy"
    # The duplicate has its own copy of the instance.
    layout = client.get(f"/api/tabs/{dup}/layout").json()
    assert len(layout["instances"]) == 1

    client.post("/api/tabs/alpha/rename", json={"new": "beta"})
    names = [t["name"] for t in client.get("/api/tabs").json()["tabs"]]
    assert "beta" in names and "alpha" not in names

    client.post("/api/tabs/reorder", json={"order": [dup, "beta"]})
    names = [t["name"] for t in client.get("/api/tabs").json()["tabs"]]
    assert names.index(dup) < names.index("beta")

    client.delete(f"/api/tabs/{dup}")
    assert dup not in [t["name"] for t in client.get("/api/tabs").json()["tabs"]]


# --- instances + websockets -----------------------------------------------

def test_singleton_enforced(client):
    client.post("/api/tabs/s/create")
    client.post("/api/tabs/s/select")
    first = client.post("/api/instances", json={"widget_id": "mixer"})
    assert first.status_code == 200
    second = client.post("/api/instances", json={"widget_id": "mixer"})
    assert second.status_code == 400  # singleton already present


def test_clock_pushes_over_websocket(client):
    client.post("/api/tabs/c/create")
    client.post("/api/tabs/c/select")
    rec = client.post("/api/instances", json={"widget_id": "clock"}).json()
    with client.websocket_connect(f"/api/instances/{rec['id']}/ws") as ws:
        msg = ws.receive_json()
        assert "time" in msg and "date" in msg


def test_shell_command_state_intents(client):
    client.post("/api/tabs/sc/create")
    client.post("/api/tabs/sc/select")
    rec = client.post("/api/instances", json={"widget_id": "shell-command"}).json()
    client.put(f"/api/instances/{rec['id']}/settings",
               json={"command": "echo hello", "mode": "interval", "interval": 1,
                     "display": "text"})
    with client.websocket_connect(f"/api/instances/{rec['id']}/ws") as ws:
        # First frame is the replayed initial state; keep reading until the run lands.
        raw = None
        for _ in range(5):
            msg = ws.receive_json()
            assert "__state__" in msg
            raw = msg["__state__"]["raw"]
            if raw == "hello":
                break
        assert raw == "hello"


def test_shell_command_on_click_does_not_autorun_but_run_intent_works(client):
    client.post("/api/tabs/oc/create")
    client.post("/api/tabs/oc/select")
    rec = client.post("/api/instances", json={"widget_id": "shell-command"}).json()
    # Switching to on-click must NOT execute the command, but must push a state
    # carrying the new mode so the frontend becomes clickable.
    client.put(f"/api/instances/{rec['id']}/settings",
               json={"command": "echo clicked", "mode": "on-click", "display": "text"})
    with client.websocket_connect(f"/api/instances/{rec['id']}/ws") as ws:
        state = ws.receive_json()["__state__"]
        assert state["mode"] == "on-click"
        assert state["value"] is None          # did not auto-run on settings change
        # A click sends the run intent → the command actually runs.
        ws.send_json({"__intent__": {"type": "run", "payload": {}}})
        raw = None
        for _ in range(5):
            raw = ws.receive_json()["__state__"]["raw"]
            if raw == "clicked":
                break
        assert raw == "clicked"


def test_settings_reflected_in_layout(client):
    client.post("/api/tabs/p/create")
    client.post("/api/tabs/p/select")
    rec = client.post("/api/instances", json={"widget_id": "clock"}).json()
    client.put(f"/api/instances/{rec['id']}/settings", json={"hour24": False})
    layout = client.get("/api/tabs/p/layout").json()
    inst = next(i for i in layout["instances"] if i["id"] == rec["id"])
    assert inst["settings"] == {"hour24": False}


# --- explicit save/load model (replaces SPEC §6 auto-save, user request) ---

def test_no_autosave_until_save(client):
    from widget_dashboard.app import profile_store
    client.post("/api/tabs/ns/create")
    client.post("/api/tabs/ns/select")
    client.post("/api/instances", json={"widget_id": "clock"})

    # The tab is now dirty and nothing is on disk yet.
    st = {t["name"]: t for t in client.get("/api/tabs").json()["tabs"]}
    assert st["ns"]["dirty"] is True
    assert len(profile_store.load("ns").instances) == 0

    # Saving clears dirty and writes to disk.
    client.post("/api/tabs/ns/save")
    st = {t["name"]: t for t in client.get("/api/tabs").json()["tabs"]}
    assert st["ns"]["dirty"] is False
    assert len(profile_store.load("ns").instances) == 1


def test_revert_discards_unsaved(client):
    client.post("/api/tabs/rv/create")
    client.post("/api/tabs/rv/select")
    client.post("/api/instances", json={"widget_id": "clock"})
    client.post("/api/tabs/rv/save")                       # 1 instance saved
    client.post("/api/instances", json={"widget_id": "system-stats"})  # 2nd, unsaved
    assert len(client.get("/api/tabs/rv/layout").json()["instances"]) == 2

    reverted = client.post("/api/tabs/rv/revert").json()
    assert len(reverted["instances"]) == 1                 # back to the saved version


def test_preset_save_and_load_with_fresh_ids(client):
    client.post("/api/tabs/src/create")
    client.post("/api/tabs/src/select")
    client.post("/api/instances", json={"widget_id": "clock"})
    client.post("/api/presets", json={"name": "p1", "from_tab": "src"})
    assert "p1" in client.get("/api/presets").json()["presets"]

    client.post("/api/tabs/dst/create")
    client.post("/api/tabs/dst/select")
    loaded = client.post("/api/tabs/dst/load-preset", json={"preset": "p1"}).json()
    assert len(loaded["instances"]) == 1

    # Loaded instances get fresh ids (not the source's), and the tab is dirty.
    src_ids = {i["id"] for i in client.get("/api/tabs/src/layout").json()["instances"]}
    dst_ids = {i["id"] for i in loaded["instances"]}
    assert src_ids.isdisjoint(dst_ids)
    assert next(t for t in loaded["tabs"] if t["name"] == "dst")["dirty"] is True


# --- triggers (SPEC §4.6, docs/triggers.md) -------------------------------

def test_fire_reaches_system_channel_with_templated_toast(client):
    client.post("/api/tabs/t/create")
    client.post("/api/tabs/t/select")
    rec = client.post("/api/instances", json={"widget_id": "clock"}).json()
    # Configure a response with a templated toast.
    client.put(f"/api/instances/{rec['id']}/response", json={
        "badge": True,
        "toast": {"enabled": True, "text": "hi {who}"},
    })
    with client.websocket_connect("/api/system/ws") as sysws:
        # Drive a fire through the dashboard (what ctx.fire() does) from inside
        # the app's event loop, via the TestClient's portal.
        from widget_dashboard.app import dashboard
        client.portal.call(dashboard.handle_fire, rec["id"], {"who": "bob"})
        evt = sysws.receive_json()
        assert evt["type"] == "trigger"
        assert evt["response"]["toast"]["text"] == "hi bob"
        assert evt["response"]["badge"] is True


# --- config ----------------------------------------------------------------

def test_config_roundtrip(client):
    client.put("/api/config", json={"dashboard_monitor": 2})
    assert client.get("/api/config").json()["dashboard_monitor"] == 2


# --- packaging (SPEC §10) -------------------------------------------------

def test_pack_validate_upload_install(client, tmp_path):
    from widget_dashboard import packaging
    from widget_dashboard.paths import BUILTIN_WIDGETS_DIR

    # Pack the built-in clock into a .wdwidget, then re-upload+install it.
    pkg = packaging.pack(BUILTIN_WIDGETS_DIR / "clock", tmp_path)
    assert pkg.suffix == ".wdwidget"
    result = packaging.validate(pkg)
    assert result.ok and result.widget_id == "clock"

    with open(pkg, "rb") as f:
        up = client.post("/api/widgets/upload",
                         files={"file": ("clock.wdwidget", f, "application/octet-stream")})
    body = up.json()
    assert body["ok"] and body["widget_id"] == "clock"
    assert "subprocess" in body["permissions"]

    installed = client.post("/api/widgets/install", json={"staged": body["staged"]})
    assert installed.json()["ok"]


# --- event bus + host service scoping (SPEC §4.3, §5) ---------------------

def test_event_bus_enforces_declared_sources():
    import asyncio

    from widget_dashboard.events import EventBus

    async def go():
        bus = EventBus(declared=["timer"])
        # An undeclared source is refused...
        with pytest.raises(PermissionError):
            await bus.subscribe("process", {}, lambda e: None)

        # ...a declared one fires its handler.
        fired = asyncio.Event()
        captured = {}

        async def handler(evt):
            captured.update(evt)
            fired.set()

        await bus.subscribe("timer", {"interval": 0.05}, handler)
        await asyncio.wait_for(fired.wait(), timeout=2)
        await bus.cancel_all()
        assert "now" in captured

    asyncio.run(go())


def test_clipboard_set_text_does_not_block_on_xclip():
    # Setting an X selection keeps xclip alive to own it; set_text must NOT wait
    # for it to exit (that blocked every clipboard copy after the first).
    import asyncio

    from widget_dashboard.host_services import ClipboardService

    class FakeStdin:
        def write(self, b): pass
        def close(self): pass

    class FakeProc:
        stdin = FakeStdin()
        async def wait(self):
            await asyncio.sleep(3600)        # xclip "stays alive" — would block

    async def fake_exec(*a, **k):
        return FakeProc()

    async def go():
        orig = asyncio.create_subprocess_exec
        asyncio.create_subprocess_exec = fake_exec
        try:
            # Must return promptly despite the never-finishing process.
            await asyncio.wait_for(ClipboardService().set_text("hi"), timeout=2)
        finally:
            asyncio.create_subprocess_exec = orig

    asyncio.run(go())


def test_host_services_scoped_to_manifest():
    from widget_dashboard.host_services import HostServices

    class FakeInstance:
        dashboard = None
        instance_id = "x"

    hs = HostServices(FakeInstance(), declared=["clipboard"])
    # Declared service is reachable.
    assert hs.clipboard is not None
    # Undeclared service is refused, not silently created.
    with pytest.raises(PermissionError):
        _ = hs.windows


# --- input-safety guards (code review) ------------------------------------

def test_packaging_rejects_unsafe_zip_member(tmp_path):
    """A .wdwidget whose members would extract outside the target dir is
    refused at validate(), not silently sanitized."""
    import zipfile

    from widget_dashboard import packaging

    evil = tmp_path / "evil.wdwidget"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("widget.json", '{"id": "evil"}')
        zf.writestr("backend.py", "")
        zf.writestr("frontend.js", "")
        zf.writestr("../../escape.txt", "pwned")
    result = packaging.validate(evil)
    assert not result.ok
    assert "unsafe path" in (result.error or "")


def test_profile_name_guard_blocks_traversal(client):
    """Tab/preset names that contain a path separator can't escape the store —
    the store raises and the API turns it into a 400, not a 500 or a write."""
    from widget_dashboard.profiles import ProfileStore, _safe_name

    for bad in ("../evil", "a/b", "..", "x\\y"):
        with pytest.raises(ValueError):
            _safe_name(bad)

    client.post("/api/tabs/safe/create")
    r = client.post("/api/tabs/safe/rename", json={"new": "../../evil"})
    assert r.status_code == 400


def test_upload_filename_is_basenamed(client, tmp_path):
    """A traversal in the uploaded filename is reduced to its basename so the
    staged file can't land outside the incoming dir."""
    from widget_dashboard import packaging
    from widget_dashboard.paths import BUILTIN_WIDGETS_DIR

    pkg = packaging.pack(BUILTIN_WIDGETS_DIR / "clock", tmp_path)
    with open(pkg, "rb") as f:
        up = client.post(
            "/api/widgets/upload",
            files={"file": ("../../../pwn.wdwidget", f, "application/octet-stream")},
        )
    body = up.json()
    assert body["ok"]
    assert "/" not in body["staged"] and ".." not in body["staged"]
