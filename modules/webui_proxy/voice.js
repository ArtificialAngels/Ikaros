/**
 * Icarus Voice Plugin — Real-time voice dialogue for Hermes Web UI
 * Injected by webui_proxy into the SPA at load time.
 *
 * Adds a floating microphone button → WebSocket to bridge → Whisper API (STT)
 * → LLM → edge-tts (TTS streaming) → audio playback.
 *
 * Protocol (see bridge/voice_server.py):
 *   Client → bridge:  JSON {action:"start"|"stop"|"ping"} + BINARY audio chunks
 *   Bridge → client:  JSON {type:"status"|"transcription"|"thinking"|"done"|"error"}
 *                      + BINARY mp3 audio chunks
 */
(function () {
  "use strict";

  // ---- Config ----
  var WS_URL = "ws://127.0.0.1:7860/v1/voice/ws";
  var BRIDGE_URL = "http://127.0.0.1:7860";
  var SESSION_ID = "voice_" + Date.now();
  var STATE = "idle"; // idle | listening | thinking | speaking

  // ---- DOM setup ----
  var container = document.createElement("div");
  container.id = "icarus-voice-container";
  container.innerHTML =
    '<style>' +
    '#icarus-voice-btn {' +
    '  position: fixed; bottom: 24px; right: 24px; z-index: 99999;' +
    '  width: 56px; height: 56px; border-radius: 50%; border: none;' +
    '  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);' +
    '  color: white; font-size: 24px; cursor: pointer;' +
    '  box-shadow: 0 4px 15px rgba(102,126,234,0.4);' +
    '  display: flex; align-items: center; justify-content: center;' +
    '  transition: all 0.3s ease;' +
    '}' +
    '#icarus-voice-btn:hover { transform: scale(1.1); box-shadow: 0 6px 20px rgba(102,126,234,0.6); }' +
    '#icarus-voice-btn.listening { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); animation: pulse 1.5s infinite; }' +
    '#icarus-voice-btn.thinking { background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); }' +
    '#icarus-voice-btn.speaking { background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%); }' +
    '@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(245,87,108,0.7); } 70% { box-shadow: 0 0 0 20px rgba(245,87,108,0); } 100% { box-shadow: 0 0 0 0 rgba(245,87,108,0); } }' +
    '#icarus-voice-status {' +
    '  position: fixed; bottom: 88px; right: 24px; z-index: 99999;' +
    '  background: rgba(0,0,0,0.8); color: white; padding: 8px 16px; border-radius: 20px;' +
    '  font-size: 13px; max-width: 260px; display: none;' +
    '  backdrop-filter: blur(10px);' +
    '}' +
    '</style>' +
    '<button id="icarus-voice-btn" title="语音对话 (实时)">🎤</button>' +
    '<div id="icarus-voice-status"></div>';

  document.body.appendChild(container);

  var btn = document.getElementById("icarus-voice-btn");
  var statusEl = document.getElementById("icarus-voice-status");

  function setStatus(text, visible) {
    statusEl.textContent = text;
    statusEl.style.display = visible !== false ? "block" : "none";
  }

  function setState(state) {
    STATE = state;
    btn.className = state;
    if (state === "listening") {
      btn.textContent = "🔴";
      setStatus("🎙️ 聆听中… 说话吧");
    } else if (state === "thinking") {
      btn.textContent = "🧠";
      setStatus("🧠 思考中…");
    } else if (state === "speaking") {
      btn.textContent = "🔊";
      setStatus("🔊 回复中…");
    } else {
      btn.textContent = "🎤";
      btn.className = "";
      setStatus("", false);
    }
  }

  // ---- WebSocket + MediaRecorder ----
  var ws = null;
  var mediaRecorder = null;
  var stream = null;
  var audioCtx = null;

  function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) return;
    ws = new WebSocket(WS_URL);

    ws.onopen = function () {
      // Start session
      ws.send(JSON.stringify({ action: "start", session_id: SESSION_ID }));
      setState("listening");
      startRecording();
    };

    ws.onmessage = function (evt) {
      if (evt.data instanceof Blob) {
        // Binary audio chunk from TTS
        var blob = evt.data;
        var audioUrl = URL.createObjectURL(blob);
        var audio = new Audio(audioUrl);
        audio.onended = function () {
          URL.revokeObjectURL(audioUrl);
          // If we're not processing, go back to idle
          if (STATE === "speaking") {
            setState("idle");
          }
        };
        audio.play().catch(function (err) {
          console.warn("[icarus-voice] audio playback error:", err);
        });
        setState("speaking");
        return;
      }

      try {
        var msg = JSON.parse(evt.data);
      } catch (e) {
        return;
      }

      switch (msg.type) {
        case "status":
          setStatus(msg.message);
          break;
        case "transcription":
          setStatus("📝 你说: " + msg.text);
          // Optionally insert into chat input
          insertIntoChat(msg.text);
          break;
        case "thinking":
          setState("thinking");
          break;
        case "done":
          if (msg.text) {
            setStatus("✅ " + msg.text.slice(0, 60) + (msg.text.length > 60 ? "…" : ""));
          }
          // Auto-return to listening for continuous conversation
          if (ws && ws.readyState === WebSocket.OPEN) {
            setTimeout(function () {
              setState("listening");
              startRecording();
            }, 800);
          }
          break;
        case "error":
          setStatus("⚠️ " + msg.message);
          setState("idle");
          break;
        case "pong":
          break;
      }
    };

    ws.onclose = function () {
      stopRecording();
      setState("idle");
      setStatus("🔌 已断开", true);
      // Auto-reconnect after 3s
      setTimeout(function () {
        if (STATE === "idle") {
          setStatus("🔄 重连中…", true);
          connect();
        }
      }, 3000);
    };

    ws.onerror = function () {
      setStatus("⚠️ 连接失败", true);
    };
  }

  function disconnect() {
    if (ws) {
      ws.send(JSON.stringify({ action: "stop" }));
      ws.close();
      ws = null;
    }
    stopRecording();
    setState("idle");
  }

  // ---- MediaRecorder (mic → WebSocket) ----
  function startRecording() {
    if (mediaRecorder && mediaRecorder.state === "recording") return;

    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then(function (s) {
        stream = s;
        var options = { mimeType: "audio/webm;codecs=opus" };
        // Fallback if webm not supported
        if (!MediaRecorder.isTypeSupported(options.mimeType)) {
          options = { mimeType: "audio/webm" };
          if (!MediaRecorder.isTypeSupported(options.mimeType)) {
            options = {}; // let browser decide
          }
        }
        mediaRecorder = new MediaRecorder(stream, options);

        mediaRecorder.ondataavailable = function (evt) {
          if (evt.data.size > 0 && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(evt.data);
          }
        };

        mediaRecorder.start(250); // send chunks every 250ms
      })
      .catch(function (err) {
        setStatus("⚠️ 麦克风权限被拒: " + err.message, true);
        setState("idle");
      });
  }

  function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      mediaRecorder.stop();
    }
    if (stream) {
      stream.getTracks().forEach(function (t) { t.stop(); });
      stream = null;
    }
    mediaRecorder = null;
  }

  // ---- Insert transcription into the chat input ----
  function insertIntoChat(text) {
    // Try common webui selectors for the chat textarea/input
    var input = document.querySelector(
      'textarea[placeholder*="消息"], ' +
      'textarea[placeholder*="Type"], ' +
      'div[contenteditable="true"], ' +
      'input[type="text"][placeholder*="chat"], ' +
      '#chat-input, ' +
      '.chat-input textarea'
    );
    if (input) {
      if (input.tagName === "TEXTAREA" || input.tagName === "INPUT") {
        input.value = (input.value ? input.value + "\n" : "") + text;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      } else if (input.isContentEditable) {
        input.textContent = (input.textContent ? input.textContent + "\n" : "") + text;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
  }

  // ---- Button click ----
  btn.addEventListener("click", function () {
    if (STATE === "idle") {
      connect();
    } else {
      disconnect();
    }
  });

  // ---- Heartbeat ----
  setInterval(function () {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: "ping" }));
    }
  }, 30000);

  // ---- Startup: add a small delay to ensure SPA loaded ----
  setStatus("🎤 点右下角麦克风说话", true);
  console.log("[icarus-voice] plugin loaded. Click 🎤 to start voice chat.");
})();
