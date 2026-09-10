"""
SpeakPaste MCP Server
Exposes the speak_to_user tool to multimodal AI models.
When called by an AI model, forwards the text over WebSocket to SpeakPaste
for real-time speech synthesis and playback.
"""

import sys
import json
import asyncio
import websockets

DEFAULT_WS_PORT = 9137

async def _send_speak_to_speakpaste(text: str, session_title: str = "Assistant", port: int = DEFAULT_WS_PORT) -> str:
    uri = f"ws://localhost:{port}"
    try:
        async with websockets.connect(uri, open_timeout=2.5) as ws:
            payload = json.dumps({
                "action": "speak",
                "voice_text": text,
                "session_title": session_title
            })
            await ws.send(payload)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.5)
                res = json.loads(raw)
                if res.get("status") == "ok":
                    return f"Voice output queued for [{session_title}] and playing to user via SpeakPaste."
            except (asyncio.TimeoutError, json.JSONDecodeError):
                return "Voice output dispatched to SpeakPaste."
            return "Voice output dispatched to SpeakPaste."
    except Exception as e:
        return f"SpeakPaste desktop app is not connected on {uri}. Make sure SpeakPaste is running. (Error: {e})"


TOOL_DOC = (
    "Speak text directly to the user in a natural colloquial voice via SpeakPaste.\n\n"
    "CRITICAL USAGE RULE:\n"
    "- Call this tool ONLY IF the user explicitly requested voice, asked for a voice message, "
    "or asked to hear spoken response (e.g. 'با ویس بگو', 'ویس بفرست', 'speak to me', 'read aloud').\n"
    "- NEVER call this tool unsolicited, unprompted, or inside background subagents unless the user asked.\n"
    "- Always pass 'session_title' indicating which project, session, or agent is sending the voice."
)


def create_mcp_app():
    # Supports both MCP 2.x (MCPServer) and MCP 1.x (FastMCP)
    try:
        from mcp.server.mcpserver import MCPServer
        server = MCPServer("speakpaste")

        @server.tool(description=TOOL_DOC)
        def speak_to_user(voice_text: str, session_title: str = "Assistant") -> str:
            """Speak text directly to the user in a natural colloquial voice via SpeakPaste."""
            return asyncio.run(_send_speak_to_speakpaste(voice_text, session_title=session_title))

        return server
    except (ImportError, ModuleNotFoundError):
        from mcp.server.fastmcp import FastMCP
        server = FastMCP("speakpaste")

        @server.tool(description=TOOL_DOC)
        def speak_to_user(voice_text: str, session_title: str = "Assistant") -> str:
            """Speak text directly to the user in a natural colloquial voice via SpeakPaste."""
            return asyncio.run(_send_speak_to_speakpaste(voice_text, session_title=session_title))

        return server


if __name__ == "__main__":
    app = create_mcp_app()
    app.run()

