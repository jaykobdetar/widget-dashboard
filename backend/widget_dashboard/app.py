"""
HTTP + WebSocket surface (docs/architecture.md "Communication").

REST for layout/registry/instance/tab/config management; one websocket per
widget instance for live data; one system websocket for shell-level events
(toasts, badges, bounce notices, layout actions). Also serves the frontend
shell and each widget's static files.

Run with:  python -m widget_dashboard.app
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import packaging
from .config import Config
from .dashboard import Dashboard
from .download_watch import watch_for_widget
from .paths import (
    BUILTIN_WIDGETS_DIR,
    CONFIG_FILE,
    FRONTEND_DIR,
    INCOMING_WIDGETS_DIR,
    PRESETS_DIR,
    PROFILES_DIR,
    USER_WIDGETS_DIR,
    WIDGET_STATE_DIR,
)
from .profiles import PresetStore, ProfileStore
from .registry import WidgetRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("app")

config = Config(CONFIG_FILE)
registry = WidgetRegistry(BUILTIN_WIDGETS_DIR, USER_WIDGETS_DIR)
profile_store = ProfileStore(PROFILES_DIR)
preset_store = PresetStore(PRESETS_DIR)
dashboard = Dashboard(registry, profile_store, WIDGET_STATE_DIR, config, preset_store)

app = FastAPI(title="Widget Dashboard")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await dashboard.startup()
    yield
    await dashboard.shutdown()


app.router.lifespan_context = lifespan


@app.middleware("http")
async def no_cache_frontend(request, call_next):
    """The dashboard's own code (shell + widget frontends) must never be served
    stale: ES-module imports are cached per page load, so without revalidation a
    backend update wouldn't show until a hard refresh. Vendored libraries under
    /vendor (version-pinned) may still cache normally."""
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/vendor/") and (
        path == "/"
        or path.endswith((".js", ".css", ".html"))
    ):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# --- registry + picker ---

@app.get("/api/widgets")
async def list_widgets() -> JSONResponse:
    return JSONResponse(registry.picker_list())


@app.post("/api/widgets/rescan")
async def rescan() -> JSONResponse:
    await dashboard.rescan()
    return JSONResponse(registry.picker_list())


# --- tabs ---

@app.get("/api/tabs")
async def get_tabs() -> JSONResponse:
    return JSONResponse({
        "tabs": dashboard.tab_states(),
        "selected": dashboard.selected_tab,
    })


@app.post("/api/tabs/{name}/select")
async def select_tab(name: str) -> JSONResponse:
    await dashboard.select_tab(name)
    return await _tab_response(name)


@app.post("/api/tabs/{name}/enable")
async def enable_tab(name: str) -> JSONResponse:
    await dashboard.enable_tab(name)
    return JSONResponse({"tabs": dashboard.tab_states()})


@app.post("/api/tabs/{name}/disable")
async def disable_tab(name: str) -> JSONResponse:
    await dashboard.disable_tab(name)
    return JSONResponse({"tabs": dashboard.tab_states()})


@app.post("/api/tabs/{name}/create")
async def create_tab(name: str) -> JSONResponse:
    profile_store.create_empty(name)
    return JSONResponse({"tabs": dashboard.tab_states()})


@app.post("/api/tabs/{name}/rename")
async def rename_tab(name: str, body: dict) -> JSONResponse:
    new = (body.get("new") or "").strip()
    if not new:
        return JSONResponse({"error": "name required"}, status_code=400)
    dashboard.rename_tab(name, new)
    return JSONResponse({"tabs": dashboard.tab_states(), "selected": dashboard.selected_tab})


@app.post("/api/tabs/{name}/duplicate")
async def duplicate_tab(name: str) -> JSONResponse:
    new_name = dashboard.duplicate_tab(name)
    return JSONResponse({"tabs": dashboard.tab_states(), "new": new_name})


@app.delete("/api/tabs/{name}")
async def delete_tab(name: str) -> JSONResponse:
    await dashboard.delete_tab(name)
    return JSONResponse({"tabs": dashboard.tab_states(), "selected": dashboard.selected_tab})


@app.post("/api/tabs/reorder")
async def reorder_tabs(body: dict) -> JSONResponse:
    dashboard.reorder_tabs(body.get("order", []))
    return JSONResponse({"tabs": dashboard.tab_states()})


# --- explicit save / load / revert + preset library ---

@app.post("/api/tabs/{name}/save")
async def save_tab(name: str) -> JSONResponse:
    dashboard.save_tab(name)
    return JSONResponse({"tabs": dashboard.tab_states()})


@app.post("/api/tabs/{name}/revert")
async def revert_tab(name: str) -> JSONResponse:
    await dashboard.revert_tab(name)
    return await _tab_response(name)


@app.post("/api/tabs/{name}/load-preset")
async def load_preset(name: str, body: dict) -> JSONResponse:
    await dashboard.load_preset(name, body["preset"])
    return await _tab_response(name)


@app.get("/api/presets")
async def list_presets() -> JSONResponse:
    return JSONResponse({"presets": dashboard.list_presets()})


@app.post("/api/presets")
async def save_preset(body: dict) -> JSONResponse:
    name = (body.get("name") or "").strip()
    from_tab = body.get("from_tab")
    if not name or not from_tab:
        return JSONResponse({"error": "name and from_tab required"}, status_code=400)
    dashboard.save_as_preset(name, from_tab)
    return JSONResponse({"presets": dashboard.list_presets()})


@app.delete("/api/presets/{name}")
async def delete_preset(name: str) -> JSONResponse:
    dashboard.delete_preset(name)
    return JSONResponse({"presets": dashboard.list_presets()})


async def _tab_response(name: str) -> JSONResponse:
    """The selected tab's full layout, for the frontend to render."""
    profile = dashboard._profile(name)
    return JSONResponse({
        "name": profile.name,
        "grid": {"columns": profile.columns, "row_height": profile.row_height},
        "instances": [r.to_json() for r in profile.instances],
        "tabs": dashboard.tab_states(),
    })


@app.get("/api/tabs/{name}/layout")
async def get_layout(name: str) -> JSONResponse:
    return await _tab_response(name)


# --- instances on the selected tab ---

@app.post("/api/instances")
async def add_instance(body: dict) -> JSONResponse:
    rec = await dashboard.add_widget(body["widget_id"])
    if rec is None:
        return JSONResponse({"error": "could not add widget"}, status_code=400)
    return JSONResponse(rec.to_json())


@app.delete("/api/instances/{instance_id}")
async def remove_instance(instance_id: str) -> JSONResponse:
    await dashboard.remove_widget(instance_id)
    return JSONResponse({"ok": True})


@app.put("/api/instances/{instance_id}/settings")
async def set_settings(instance_id: str, body: dict) -> JSONResponse:
    await dashboard.update_instance_settings(instance_id, body)
    return JSONResponse({"ok": True})


@app.get("/api/instances/{instance_id}/response")
async def get_response(instance_id: str) -> JSONResponse:
    return JSONResponse(dashboard.get_instance_response(instance_id))


@app.put("/api/instances/{instance_id}/response")
async def set_response(instance_id: str, body: dict) -> JSONResponse:
    dashboard.update_instance_response(instance_id, body)
    return JSONResponse({"ok": True})


@app.put("/api/instances/{instance_id}/color")
async def set_color(instance_id: str, body: dict) -> JSONResponse:
    dashboard.update_instance_color(instance_id, body.get("color", ""))
    return JSONResponse({"ok": True})


@app.put("/api/layout")
async def put_layout(body: dict) -> JSONResponse:
    dashboard.update_layout(body.get("positions", []))
    return JSONResponse({"ok": True})


# --- config ---

@app.get("/api/config")
async def get_config() -> JSONResponse:
    return JSONResponse(config.as_dict())


@app.put("/api/config")
async def set_config(body: dict) -> JSONResponse:
    for k, v in body.items():
        config.set(k, v)
    return JSONResponse(config.as_dict())


@app.get("/api/system/monitors")
async def system_monitors() -> JSONResponse:
    """Connected monitors (for widgets/settings that label or target them)."""
    from . import sysutil
    dash = config.get("dashboard_monitor", 0)
    mons = await sysutil.monitors()
    return JSONResponse([
        {
            "index": m.index, "name": m.name, "primary": m.primary,
            "is_dashboard": m.index == dash,
            "geometry": {"x": m.x, "y": m.y, "w": m.w, "h": m.h},
        }
        for m in mons
    ])


# --- lockdown ---

@app.post("/api/lockdown/pause")
async def lockdown_pause(body: dict) -> JSONResponse:
    await dashboard.lockdown.pause(int(body.get("minutes", 5)))
    return JSONResponse({"ok": True, "connected": dashboard.lockdown.connected})


@app.post("/api/lockdown/resume")
async def lockdown_resume() -> JSONResponse:
    await dashboard.lockdown.resume()
    return JSONResponse({"ok": True})


# --- packaging: export / install / install-next-download ---

@app.get("/api/widgets/{widget_id}/export")
async def export_widget(widget_id: str) -> Response:
    rw = registry.get(widget_id)
    if rw is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    out = packaging.pack(rw.manifest.path, INCOMING_WIDGETS_DIR)
    return FileResponse(out, media_type="application/octet-stream",
                        filename=out.name)


@app.post("/api/widgets/upload")
async def upload_widget(file: UploadFile) -> JSONResponse:
    """Stage an uploaded .wdwidget and return its declared capabilities for the
    permission-confirm dialog (docs/packaging.md 10.2). Does NOT install yet."""
    staged = INCOMING_WIDGETS_DIR / (file.filename or "upload.wdwidget")
    staged.write_bytes(await file.read())
    result = packaging.validate(staged)
    if not result.ok:
        staged.unlink(missing_ok=True)
        return JSONResponse({"ok": False, "error": result.error}, status_code=400)
    return JSONResponse({
        "ok": True,
        "staged": staged.name,
        "widget_id": result.widget_id,
        "version": result.version,
        "permissions": result.permissions,
        "host_services": result.host_services,
        "requires": result.requires,
        "already_installed": registry.get(result.widget_id) is not None,
    })


@app.post("/api/widgets/install")
async def install_widget(body: dict) -> JSONResponse:
    """Complete an install the user has confirmed in the permission dialog."""
    staged = INCOMING_WIDGETS_DIR / body["staged"]
    if not staged.exists():
        return JSONResponse({"error": "staged file gone"}, status_code=404)
    try:
        packaging.install(staged, USER_WIDGETS_DIR, overwrite=True)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    finally:
        staged.unlink(missing_ok=True)
    await dashboard.rescan()
    return JSONResponse({"ok": True, "widgets": registry.picker_list()})


@app.post("/api/widgets/install-next-download")
async def install_next_download() -> JSONResponse:
    """Arm the single-shot, time-boxed Downloads watcher (docs/packaging.md
    10.3). When a *.wdwidget lands it is staged and the shell is prompted to
    confirm; nothing auto-installs."""
    import asyncio

    async def run() -> None:
        staged = await watch_for_widget(INCOMING_WIDGETS_DIR)
        if staged is None:
            await dashboard.system.broadcast({"type": "install_timeout"})
            return
        result = packaging.validate(staged)
        if not result.ok:
            staged.unlink(missing_ok=True)
            await dashboard.system.broadcast(
                {"type": "install_error", "error": result.error})
            return
        await dashboard.system.broadcast({
            "type": "install_prompt",
            "staged": staged.name,
            "widget_id": result.widget_id,
            "version": result.version,
            "permissions": result.permissions,
            "host_services": result.host_services,
            "requires": result.requires,
            "already_installed": registry.get(result.widget_id) is not None,
        })

    asyncio.create_task(run())
    return JSONResponse({"watching": True})


# --- serving widget frontends + assets ---

def _serve_widget_file(widget_id: str, filename: str, media_type: str,
                       empty: str = "") -> Response:
    rw = registry.get(widget_id)
    if rw is None:
        return Response("not found", status_code=404)
    f = rw.manifest.path / filename
    if not f.exists():
        return Response(empty, media_type=media_type)
    return FileResponse(f, media_type=media_type)


@app.get("/api/widgets/{widget_id}/frontend.js")
async def widget_frontend(widget_id: str) -> Response:
    return _serve_widget_file(widget_id, "frontend.js", "text/javascript",
                              "// widget has no frontend.js")


@app.get("/api/widgets/{widget_id}/settings.js")
async def widget_settings(widget_id: str) -> Response:
    return _serve_widget_file(widget_id, "settings.js", "text/javascript",
                              "export default null;")


@app.get("/api/widgets/{widget_id}/style.css")
async def widget_style(widget_id: str) -> Response:
    return _serve_widget_file(widget_id, "style.css", "text/css")


@app.get("/api/widgets/{widget_id}/assets/{path:path}")
async def widget_asset(widget_id: str, path: str) -> Response:
    rw = registry.get(widget_id)
    if rw is None:
        return Response("not found", status_code=404)
    f = (rw.manifest.path / "assets" / path).resolve()
    # Keep the path inside the widget's assets dir.
    if not str(f).startswith(str((rw.manifest.path / "assets").resolve())):
        return Response("forbidden", status_code=403)
    if not f.exists():
        return Response("not found", status_code=404)
    return FileResponse(f)


# --- per-instance websocket ---

@app.websocket("/api/instances/{instance_id}/ws")
async def instance_ws(ws: WebSocket, instance_id: str) -> None:
    await ws.accept()
    inst = dashboard.instances.get(instance_id)
    if inst is None:
        await ws.close(code=4404)
        return
    inst.attach_ws(ws)
    try:
        while True:
            msg = await ws.receive_json()
            await inst.handle_frontend_message(msg)
    except WebSocketDisconnect:
        pass
    finally:
        inst.detach_ws(ws)


# --- system websocket (shell-level events) ---

@app.websocket("/api/system/ws")
async def system_ws(ws: WebSocket) -> None:
    await ws.accept()
    dashboard.system.attach(ws)
    try:
        while True:
            await ws.receive_text()  # shell doesn't send; keep the socket open
    except WebSocketDisconnect:
        pass
    finally:
        dashboard.system.detach(ws)


# --- frontend shell (mounted last so /api/* wins) ---

app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
