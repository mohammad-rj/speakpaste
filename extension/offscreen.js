/**
 * SpeakPaste Bridge — offscreen.js
 * Runs hidden inside Chrome (Manifest V3 Offscreen Document).
 * Connects to the Python WebSocket server and drives Web Speech API.
 *
 * Protocol (JSON over WebSocket):
 *   Python → Extension : { cmd: "start", lang: "fa" }
 *                        { cmd: "stop" }
 *   Extension → Python : { text: "recognized text" }
 *                        { error: "error-code" }
 */

// Must match WS_PORT in .env (default 9137)
const WS_PORT = 9137;

let ws          = null;
let recognition = null;
let transcript  = '';

// ─── WebSocket connection (auto-reconnect) ────────────────────────────────────

function connect() {
  ws = new WebSocket(`ws://localhost:${WS_PORT}`);

  ws.onopen = () => {
    console.log('[SpeakPaste] Connected to Python bridge');
  };

  ws.onmessage = ({ data }) => {
    let msg;
    try { msg = JSON.parse(data); } catch { return; }

    if (msg.cmd === 'start') startRecognition(msg.lang || 'fa');
    else if (msg.cmd === 'stop') stopRecognition();
  };

  ws.onclose = () => {
    console.log('[SpeakPaste] Disconnected — retrying in 2s...');
    setTimeout(connect, 2000);
  };

  ws.onerror = () => ws.close();
}

// ─── Speech Recognition ───────────────────────────────────────────────────────

function startRecognition(lang) {
  if (recognition) recognition.abort();

  transcript  = '';
  recognition = new webkitSpeechRecognition();

  recognition.continuous      = true;   // keep listening while key is held
  recognition.interimResults  = false;  // final results only
  recognition.lang            = lang;
  recognition.maxAlternatives = 1;

  recognition.onresult = ({ results, resultIndex }) => {
    for (let i = resultIndex; i < results.length; i++) {
      if (results[i].isFinal) {
        transcript += results[i][0].transcript;
      }
    }
  };

  recognition.onerror = ({ error }) => {
    console.error('[SpeakPaste] Recognition error:', error);
    send({ error });
    recognition = null;
  };

  recognition.onend = () => {
    send({ text: transcript.trim() });
    recognition = null;
  };

  recognition.start();
}

function stopRecognition() {
  if (recognition) {
    recognition.stop();   // triggers onend → send({ text })
  } else {
    send({ text: '' });   // nothing was recorded
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function send(data) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(data));
  }
}

connect();
