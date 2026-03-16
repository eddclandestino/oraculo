/**
 * ORÁCULO Main Application
 *
 * Orchestrates all components: WebSocket client, Media handler, Waveform, and UI.
 * Implements a state machine for clean UI transitions.
 *
 * States:
 *   IDLE           -> User sees "Start Session" button
 *   CONNECTING     -> WebSocket connecting + Gemini session initializing
 *   READY          -> Connected, mic off. User can enable mic/camera.
 *   LISTENING      -> Mic active, streaming audio. Waiting for user to speak.
 *   AGENT_SPEAKING -> Receiving and playing audio from Oráculo
 *   TOOL_CALLING   -> Agent is calling a function tool
 *   ERROR          -> Something went wrong
 */
import { MediaHandler } from './media-handler.js';
import { WebSocketClient } from './websocket-client.js';

// ═══════════════════════════════════════
// WAVEFORM RENDERER
// ═══════════════════════════════════════

class WaveformRenderer {
  constructor(canvasEl) {
    this.canvas = canvasEl;
    this.ctx = canvasEl.getContext('2d');
    this.analyser = null;
    this.color = '#3b82f6';
    this.isActive = false;
    this._animId = null;
    this._barWidth = 2;
    this._barGap = 1;
    this._decayBars = null;
    this._displayWidth = 0;
    this._displayHeight = 0;
    this._resize();
    window.addEventListener('resize', () => this._resize());
  }

  _resize() {
    const rect = this.canvas.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    this.canvas.width = rect.width * window.devicePixelRatio;
    this.canvas.height = rect.height * window.devicePixelRatio;
    this.ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    this._displayWidth = rect.width;
    this._displayHeight = rect.height;
  }

  setAnalyser(analyserNode, color) {
    if (!analyserNode) return;
    this.analyser = analyserNode;
    this.color = color;
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.8;
    this._decayBars = new Uint8Array(this.analyser.frequencyBinCount);
  }

  start() {
    this.isActive = true;
    if (!this._animId) this._draw();
  }

  stop() {
    this.isActive = false;
  }

  _draw() {
    this._animId = requestAnimationFrame(() => this._draw());

    const { ctx, _displayWidth: w, _displayHeight: h } = this;
    if (w === 0 || h === 0) return;
    ctx.clearRect(0, 0, w, h);

    if (!this.analyser) return;

    const bufferLength = this.analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    if (this.isActive) {
      this.analyser.getByteFrequencyData(dataArray);
    }

    // Smooth decay
    if (!this._decayBars || this._decayBars.length !== bufferLength) {
      this._decayBars = new Uint8Array(bufferLength);
    }
    for (let i = 0; i < bufferLength; i++) {
      if (dataArray[i] > this._decayBars[i]) {
        this._decayBars[i] = dataArray[i];
      } else {
        this._decayBars[i] = Math.max(0, this._decayBars[i] - 5);
      }
    }

    // Draw bars
    const totalBarWidth = this._barWidth + this._barGap;
    const barCount = Math.floor(w / totalBarWidth);
    const step = Math.max(1, Math.floor(bufferLength / barCount));

    for (let i = 0; i < barCount; i++) {
      const idx = Math.min(i * step, bufferLength - 1);
      const value = this._decayBars[idx] / 255;
      const barHeight = value * h * 0.9;

      const x = i * totalBarWidth;
      const y = h - barHeight;

      const alpha = 0.15 + value * 0.65;
      ctx.fillStyle = this.color;
      ctx.globalAlpha = alpha;
      ctx.fillRect(x, y, this._barWidth, barHeight);
    }
    ctx.globalAlpha = 1;
  }

  destroy() {
    if (this._animId) {
      cancelAnimationFrame(this._animId);
      this._animId = null;
    }
  }
}

// ═══════════════════════════════════════
// MAIN APPLICATION
// ═══════════════════════════════════════

class OraculoApp {
  constructor() {
    // State
    this._state = 'IDLE';
    this._activityLog = [];

    // DOM references
    this.$startBtn = document.getElementById('btn-start');
    this.$stopBtn = document.getElementById('btn-stop');
    this.$micBtn = document.getElementById('btn-mic');
    this.$cameraBtn = document.getElementById('btn-camera');
    this.$screenBtn = document.getElementById('btn-screen');
    this.$textInput = document.getElementById('text-input');
    this.$sendBtn = document.getElementById('btn-send');
    this.$status = document.getElementById('status-text');
    this.$statusDot = document.getElementById('status-dot');
    this.$videoPreview = document.getElementById('video-preview');
    this.$videoPlaceholder = document.getElementById('video-placeholder');
    this.$activityLog = document.getElementById('activity-log');
    this.$toolIndicator = document.getElementById('tool-indicator');
    this.$toolName = document.getElementById('tool-name');
    this.$mediaControls = document.getElementById('media-controls');

    // Waveform
    const waveformCanvas = document.getElementById('waveform-canvas');
    this._waveform = waveformCanvas ? new WaveformRenderer(waveformCanvas) : null;

    // Components (initialized per session)
    this._ws = null;
    this._media = null;

    // Bind events
    this.$startBtn.addEventListener('click', () => this.startSession());
    this.$stopBtn.addEventListener('click', () => this.stopSession());
    this.$micBtn.addEventListener('click', () => this.toggleMic());
    this.$cameraBtn.addEventListener('click', () => this.toggleCamera());
    this.$screenBtn.addEventListener('click', () => this.startScreenShare());
    this.$sendBtn.addEventListener('click', () => this.sendTextMessage());
    this.$textInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendTextMessage();
      }
    });

    this._setState('IDLE');
  }

  // ═══════════════════════════════════════
  // STATE MACHINE
  // ═══════════════════════════════════════

  _setState(state, detail = '') {
    this._state = state;
    console.log(`[App] State: ${state}${detail ? ` (${detail})` : ''}`);

    // Body state class for CSS effects (gold glow, blue glow, etc.)
    document.body.className = '';
    document.body.classList.add(`state-${state.toLowerCase().replace('_', '-')}`);

    const statusMap = {
      'IDLE':            { text: 'Ready to connect',                 dot: '#3d4f63', pulse: false },
      'CONNECTING':      { text: 'Connecting to Oráculo...',         dot: '#d4a847', pulse: true  },
      'READY':           { text: 'Connected — Enable mic to start',  dot: '#22c55e', pulse: false },
      'LISTENING':       { text: 'Listening...',                     dot: '#3b82f6', pulse: true  },
      'AGENT_SPEAKING':  { text: 'Oráculo is speaking...',           dot: '#d4a847', pulse: true  },
      'TOOL_CALLING':    { text: `Fetching data: ${detail}`,         dot: '#f59e0b', pulse: true  },
      'ERROR':           { text: `Error: ${detail}`,                 dot: '#ef4444', pulse: false },
    };

    const s = statusMap[state] || statusMap['IDLE'];
    this.$status.textContent = s.text;
    this.$statusDot.style.backgroundColor = s.dot;
    if (s.pulse) {
      this.$statusDot.style.boxShadow = `0 0 8px ${s.dot}`;
    } else {
      this.$statusDot.style.boxShadow = 'none';
    }
    this.$statusDot.classList.toggle('pulse', s.pulse);

    // Button visibility
    const isSession = !['IDLE', 'ERROR'].includes(state);
    this.$startBtn.classList.toggle('hidden', isSession);
    this.$stopBtn.classList.toggle('hidden', !isSession);
    this.$mediaControls.classList.toggle('hidden', !isSession || state === 'CONNECTING');

    // Mic button state
    if (this._media && this._media.micActive) {
      this.$micBtn.classList.add('active');
      this.$micBtn.innerHTML = '&#127908; Mic On';
    } else {
      this.$micBtn.classList.remove('active');
      this.$micBtn.innerHTML = '&#127908; Mic Off';
    }

    // Camera/screen button state
    if (this._media && this._media.videoActive) {
      if (this._media.videoMode === 'camera') {
        this.$cameraBtn.classList.add('active');
        this.$screenBtn.classList.remove('active');
      } else if (this._media.videoMode === 'screen') {
        this.$screenBtn.classList.add('active');
        this.$cameraBtn.classList.remove('active');
      }
    } else {
      this.$cameraBtn.classList.remove('active');
      this.$screenBtn.classList.remove('active');
    }

    // Tool indicator
    this.$toolIndicator.classList.toggle('hidden', state !== 'TOOL_CALLING');

    // Waveform transitions
    if (this._waveform && this._media) {
      if (state === 'LISTENING') {
        const micAnalyser = this._media.getMicAnalyser();
        if (micAnalyser) {
          this._waveform.setAnalyser(micAnalyser, '#3b82f6');
          this._waveform.start();
        }
      } else if (state === 'AGENT_SPEAKING') {
        const playbackAnalyser = this._media.getPlaybackAnalyser();
        if (playbackAnalyser) {
          this._waveform.setAnalyser(playbackAnalyser, '#d4a847');
          this._waveform.start();
        }
      } else {
        this._waveform.stop();
      }
    }
  }

  // ═══════════════════════════════════════
  // SESSION LIFECYCLE
  // ═══════════════════════════════════════

  async startSession() {
    // Guard: ignore if already connecting or connected.
    // Without this, a double-click (or a stale reconnect timer from a previous
    // WebSocketClient instance) can call startSession() twice, creating two
    // server sessions and orphaning the first WebSocket (which then
    // auto-reconnects because its _intentionalClose is never set to true).
    if (this._state !== 'IDLE' && this._state !== 'ERROR') {
      console.warn('[App] startSession() called while already in state:', this._state);
      return;
    }

    this._setState('CONNECTING');
    this._activityLog = [];
    this._updateActivityLog();

    try {
      // Initialize media handler
      this._media = new MediaHandler({
        onAudioChunk: (base64) => {
          if (this._ws && this._ws.isConnected) {
            this._ws.sendAudio(base64);
          }
        },
        onVideoFrame: (base64) => {
          if (this._ws && this._ws.isConnected) {
            this._ws.sendVideo(base64);
          }
        },
        onAudioStateChange: (playbackState) => {
          if (playbackState === 'playing' && this._state !== 'TOOL_CALLING') {
            this._setState('AGENT_SPEAKING');
          } else if (playbackState === 'idle' && this._state === 'AGENT_SPEAKING') {
            this._setState(this._media.micActive ? 'LISTENING' : 'READY');
          }
        },
      });

      // Initialize WebSocket client
      this._ws = new WebSocketClient({
        onAudio: (base64) => {
          this._media.playAudio(base64);
        },
        onText: (text) => {
          this._addActivity('text', text);
        },
        onTranscript: (text, role) => {
          this._addActivity(role === 'user' ? 'user' : 'text', text);
        },
        onToolCall: (name) => {
          this._setState('TOOL_CALLING', this._formatToolName(name));
          this._addActivity('tool', `Calling ${this._formatToolName(name)}...`);
        },
        onInterrupted: () => {
          this._media.stopPlayback();
          this._addActivity('system', 'Interrupted — listening...');
          if (this._media.micActive) {
            this._setState('LISTENING');
          }
        },
        onTurnComplete: () => {
          // Only transition if we're not already in a better state
          if (this._state === 'AGENT_SPEAKING' || this._state === 'TOOL_CALLING') {
            this._setState(this._media.micActive ? 'LISTENING' : 'READY');
          }
        },
        onStateChange: (wsState) => {
          if (wsState === 'connected' && this._state === 'CONNECTING') {
            this._setState('READY');
            this._addActivity('system', 'Connected to Oráculo');
          } else if (wsState === 'disconnected' && !['IDLE', 'ERROR'].includes(this._state)) {
            this._setState('ERROR', 'Connection lost');
            this._addActivity('error', 'Connection lost. Click Start to reconnect.');
          }
        },
        onError: (msg) => {
          this._addActivity('error', msg);
        },
        onReconnecting: (msg) => {
          this._addActivity('system', msg || 'Refreshing AI connection...');
        },
        onSessionResumed: () => {
          this._addActivity('system', 'Session resumed — context preserved');
        },
      });

      await this._ws.connect();
    } catch (err) {
      console.error('[App] Session start failed:', err);
      this._setState('ERROR', err.message);
      this._addActivity('error', `Failed to connect: ${err.message}`);
    }
  }

  stopSession() {
    if (this._media) {
      this._media.stopAll();
    }
    if (this._ws) {
      this._ws.disconnect();
    }
    if (this._waveform) {
      this._waveform.stop();
    }
    this._hideVideoPreview();
    this._setState('IDLE');
    this._addActivity('system', 'Session ended');
  }

  // ═══════════════════════════════════════
  // MIC / CAMERA / SCREEN CONTROLS
  // ═══════════════════════════════════════

  async toggleMic() {
    if (!this._media) return;

    if (this._media.micActive) {
      this._media.stopMic();
      this._setState('READY');
      this._addActivity('system', 'Microphone disabled');
    } else {
      try {
        await this._media.startMic();
        this._setState('LISTENING');
        this._addActivity('system', 'Microphone enabled — speak to Oráculo');
      } catch (err) {
        this._addActivity('error', err.message);
      }
    }
  }

  async toggleCamera() {
    if (!this._media) return;

    if (this._media.videoActive && this._media.videoMode === 'camera') {
      this._media.stopVideo();
      this._hideVideoPreview();
      this._addActivity('system', 'Camera disabled');
      this._setState(this._state);
    } else {
      try {
        const stream = await this._media.startCamera();
        this._showVideoPreview(stream);
        this._addActivity('system', 'Camera enabled — Oráculo can see your view');
        this._setState(this._state);
      } catch (err) {
        this._addActivity('error', err.message);
      }
    }
  }

  async startScreenShare() {
    if (!this._media) return;

    if (this._media.videoActive && this._media.videoMode === 'screen') {
      this._media.stopVideo();
      this._hideVideoPreview();
      this._addActivity('system', 'Screen share stopped');
      this._setState(this._state);
      return;
    }

    try {
      const stream = await this._media.startScreenShare();
      this._showVideoPreview(stream);
      this._addActivity('system', 'Screen sharing — Oráculo can see your charts');
      this._setState(this._state);
    } catch (err) {
      // User cancelled the picker — not an error
      if (err.name !== 'NotAllowedError') {
        this._addActivity('error', err.message);
      }
    }
  }

  sendTextMessage() {
    const text = this.$textInput.value.trim();
    if (!text || !this._ws || !this._ws.isConnected) return;
    this._ws.sendText(text);
    this._addActivity('user', text);
    this.$textInput.value = '';
  }

  // ═══════════════════════════════════════
  // UI HELPERS
  // ═══════════════════════════════════════

  _showVideoPreview(stream) {
    this.$videoPreview.srcObject = stream;
    this.$videoPreview.classList.remove('hidden');
    this.$videoPlaceholder.classList.add('hidden');
  }

  _hideVideoPreview() {
    this.$videoPreview.srcObject = null;
    this.$videoPreview.classList.add('hidden');
    this.$videoPlaceholder.classList.remove('hidden');
  }

  _addActivity(type, message) {
    const timestamp = new Date().toLocaleTimeString('en-US', { hour12: false });
    this._activityLog.push({ type, message, timestamp });
    if (this._activityLog.length > 50) this._activityLog.shift();
    this._updateActivityLog();
  }

  _updateActivityLog() {
    if (!this.$activityLog) return;

    const typeIcons = {
      system: '\u2699\uFE0F',
      tool:   '\uD83D\uDD27',
      text:   '\uD83D\uDCAC',
      user:   '\uD83D\uDC64',
      error:  '\u274C',
    };

    this.$activityLog.innerHTML = this._activityLog.map(entry => {
      const icon = typeIcons[entry.type] || '';
      return `<div class="log-entry" data-type="${entry.type}">
        <span class="log-time">${entry.timestamp}</span>
        <span class="log-icon">${icon}</span>
        <span class="log-message">${this._escapeHtml(entry.message)}</span>
      </div>`;
    }).join('');

    this.$activityLog.scrollTop = this.$activityLog.scrollHeight;
  }

  _formatToolName(name) {
    const names = {
      'get_stock_quote': 'Stock Quote',
      'get_market_news': 'Market News',
      'get_technical_indicators': 'Technical Indicators',
      'get_options_snapshot': 'Options Snapshot',
    };
    return names[name] || name;
  }

  _escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  window.app = new OraculoApp();
});
