import asyncio
import json
import threading
from queue import Queue, Empty
import websockets
from .config import log

_ws_loop    = None
_ws_clients = set()
_result_q   = Queue()
_on_speak_callback = None

async def _ws_handler(websocket):
    _ws_clients.add(websocket)
    log("[WebSocket] Client connected")
    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action = data.get("action") or data.get("cmd")
            if action in ("speak", "speak_to_user"):
                text = (data.get("voice_text") or data.get("text") or "").strip()
                session_title = (data.get("session_title") or data.get("session_name") or data.get("session_id") or "AI").strip()
                if text:
                    log(f"[MCP] Received speak request from [{session_title}]: {text[:60]}... ({len(text)} chars)")
                    if _on_speak_callback:
                        threading.Thread(target=_on_speak_callback, args=(text, session_title), daemon=True).start()
                    try:
                        await websocket.send(json.dumps({"status": "ok", "message": "Speaking queued"}))
                    except Exception:
                        pass
                else:
                    try:
                        await websocket.send(json.dumps({"status": "error", "message": "Empty text"}))
                    except Exception:
                        pass
                continue

            _result_q.put(data)
    except Exception as e:
        log(f"[WebSocket] Handler error: {e}")
    finally:
        _ws_clients.discard(websocket)
        log("[WebSocket] Client disconnected")


async def _ws_send(data):
    if not _ws_clients:
        return
    msg = json.dumps(data)
    await asyncio.gather(*[ws.send(msg) for ws in list(_ws_clients)], return_exceptions=True)


def google_send(data):
    if _ws_loop:
        asyncio.run_coroutine_threadsafe(_ws_send(data), _ws_loop)


def start_ws_server(port=9137, on_speak=None):
    global _ws_loop, _on_speak_callback
    _on_speak_callback = on_speak
    _ws_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_ws_loop)

    async def _serve():
        async with websockets.serve(_ws_handler, "localhost", port):
            log(f"[WebSocket] Ready on ws://localhost:{port}")
            await asyncio.Future()

    _ws_loop.run_until_complete(_serve())


def transcribe_google_ext():
    while not _result_q.empty():
        try:
            _result_q.get_nowait()
        except Empty:
            break
    if not _ws_clients:
        log("[Google-ext] Extension not connected (install & reload Chrome)")
        return None
    google_send({"cmd": "stop"})
    try:
        result = _result_q.get(timeout=10)
        text = result.get("text", "").strip()
        if result.get("error"):
            log(f"[Google-ext] Error: {result['error']}")
            return None
        if text:
            log(f">> {text}")
            return text
        return None
    except Empty:
        log("[Google-ext] Timeout")
        return None
