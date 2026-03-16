/**
 * ORÁCULO WebSocket Client
 *
 * Manages the WebSocket connection to the FastAPI backend.
 * Handles JSON message protocol, auto-reconnect, and connection state.
 *
 * Protocol (JSON over WebSocket):
 *   Client -> Server:
 *     { type: "audio", data: "<base64 PCM 16kHz 16-bit LE>" }
 *     { type: "video", data: "<base64 JPEG>" }
 *     { type: "text",  text: "user typed message" }
 *
 *   Server -> Client:
 *     { type: "audio",         data: "<base64 PCM 24kHz 16-bit LE>" }
 *     { type: "text",          text: "transcription or info" }
 *     { type: "interrupted" }
 *     { type: "tool_call",     name: "tool_name" }
 *     { type: "turn_complete" }
 *     { type: "error",         message: "error description" }
 */
export class WebSocketClient {
  constructor({
    onAudio,
    onText,
    onTranscript,
    onToolCall,
    onInterrupted,
    onTurnComplete,
    onStateChange,
    onError,
    onReconnecting,
    onSessionResumed,
  }) {
    this._onAudio = onAudio;
    this._onText = onText;
    this._onTranscript = onTranscript;
    this._onToolCall = onToolCall;
    this._onInterrupted = onInterrupted;
    this._onTurnComplete = onTurnComplete;
    this._onStateChange = onStateChange;
    this._onError = onError;
    this._onReconnecting = onReconnecting;
    this._onSessionResumed = onSessionResumed;

    this._ws = null;
    this._state = 'disconnected'; // disconnected | connecting | connected | error
    this._reconnectAttempts = 0;
    this._maxReconnectAttempts = 3;
    this._reconnectDelay = 1000; // ms, doubles on each attempt
    this._intentionalClose = false;
  }

  get state() { return this._state; }
  get isConnected() { return this._state === 'connected'; }

  async connect() {
    // Guard: also block if we're mid-handshake (CONNECTING = readyState 0).
    // The original check only blocked OPEN, so a second call while connecting
    // would create a second socket alongside the first.
    if (this._ws && (
      this._ws.readyState === WebSocket.OPEN ||
      this._ws.readyState === WebSocket.CONNECTING
    )) return;

    this._intentionalClose = false;
    this._setState('connecting');

    return new Promise((resolve, reject) => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${protocol}//${window.location.host}/ws`;

      console.log(`[WebSocket] Connecting to ${url}`);
      this._ws = new WebSocket(url);

      this._ws.onopen = () => {
        console.log('[WebSocket] Connected');
        this._setState('connected');
        this._reconnectAttempts = 0;
        resolve();
      };

      this._ws.onmessage = (event) => {
        this._handleMessage(event.data);
      };

      this._ws.onerror = (event) => {
        console.error('[WebSocket] Error:', event);
      };

      this._ws.onclose = (event) => {
        console.log(`[WebSocket] Closed (code=${event.code}, reason=${event.reason})`);
        this._setState('disconnected');

        if (!this._intentionalClose && this._reconnectAttempts < this._maxReconnectAttempts) {
          this._reconnectAttempts++;
          const delay = this._reconnectDelay * Math.pow(2, this._reconnectAttempts - 1);
          console.log(`[WebSocket] Reconnecting in ${delay}ms (attempt ${this._reconnectAttempts}/${this._maxReconnectAttempts})`);
          setTimeout(() => this.connect(), delay);
        }
      };

      // Timeout connection attempt after 10s
      setTimeout(() => {
        if (this._state === 'connecting') {
          this._ws.close();
          reject(new Error('WebSocket connection timeout'));
        }
      }, 10000);
    });
  }

  _handleMessage(raw) {
    try {
      const msg = JSON.parse(raw);

      switch (msg.type) {
        case 'audio':
          if (this._onAudio) this._onAudio(msg.data);
          break;
        case 'text':
          if (this._onText) this._onText(msg.text);
          break;
        case 'transcript':
          if (this._onTranscript) this._onTranscript(msg.text, msg.role);
          break;
        case 'tool_call':
          if (this._onToolCall) this._onToolCall(msg.name);
          break;
        case 'interrupted':
          if (this._onInterrupted) this._onInterrupted();
          break;
        case 'turn_complete':
          if (this._onTurnComplete) this._onTurnComplete();
          break;
        case 'reconnecting':
          if (this._onReconnecting) this._onReconnecting(msg.message);
          break;
        case 'session_resumed':
          if (this._onSessionResumed) this._onSessionResumed();
          break;
        case 'error':
          console.error('[WebSocket] Server error:', msg.message);
          if (this._onError) this._onError(msg.message);
          break;
        default:
          console.warn('[WebSocket] Unknown message type:', msg.type);
      }
    } catch (err) {
      console.error('[WebSocket] Failed to parse message:', err);
    }
  }

  sendAudio(base64Data) {
    this._send({ type: 'audio', data: base64Data });
  }

  sendVideo(base64Data) {
    this._send({ type: 'video', data: base64Data });
  }

  sendText(text) {
    this._send({ type: 'text', text });
  }

  _send(obj) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify(obj));
    }
  }

  _setState(state) {
    this._state = state;
    if (this._onStateChange) this._onStateChange(state);
  }

  disconnect() {
    this._intentionalClose = true;
    if (this._ws) {
      this._ws.close(1000, 'Client disconnect');
      this._ws = null;
    }
    this._setState('disconnected');
  }
}
