/**
 * ORÁCULO Media Handler
 *
 * Manages audio capture (mic -> 16kHz PCM), audio playback (24kHz PCM -> speakers),
 * and video capture (camera or screen -> 1 FPS JPEG).
 *
 * Audio Format Details:
 *   - Gemini Live API INPUT:  PCM 16-bit signed little-endian, 16000 Hz, mono
 *   - Gemini Live API OUTPUT: PCM 16-bit signed little-endian, 24000 Hz, mono
 *   - Browser AudioContext:   Float32, typically 48000 Hz
 *
 * Usage:
 *   const media = new MediaHandler({ onAudioChunk, onVideoFrame, onAudioStateChange });
 *   await media.startMic();
 *   const stream = await media.startCamera();
 *   media.playAudio(base64PcmData);
 *   media.stopAll();
 */
export class MediaHandler {
  constructor({ onAudioChunk, onVideoFrame, onAudioStateChange }) {
    // Callbacks
    this._onAudioChunk = onAudioChunk;             // (base64String) => void
    this._onVideoFrame = onVideoFrame;             // (base64String) => void
    this._onAudioStateChange = onAudioStateChange; // ('playing'|'idle') => void

    // Audio capture
    this._captureStream = null;
    this._captureContext = null;
    this._workletNode = null;
    this._micAnalyser = null;

    // Audio playback
    this._playbackContext = null;
    this._playbackQueue = [];       // Queue of Float32Array buffers
    this._isPlaying = false;
    this._currentSource = null;     // Currently playing AudioBufferSourceNode
    this._nextPlayTime = 0;         // Scheduled time for gapless playback
    this._playbackAnalyser = null;

    // Video capture
    this._videoStream = null;
    this._videoElement = null;      // Hidden <video> element for frame extraction
    this._canvas = null;
    this._canvasCtx = null;
    this._frameInterval = null;     // setInterval ID for 1 FPS capture
    this._VIDEO_FPS = 1;
    this._VIDEO_QUALITY = 0.7;      // JPEG quality (0-1)
    this._VIDEO_MAX_WIDTH = 1024;   // Max dimension to keep frames small

    // State
    this.micActive = false;
    this.videoActive = false;
    this.videoMode = null;          // 'camera' | 'screen' | null
  }

  // ═══════════════════════════════════════
  // MICROPHONE CAPTURE
  // ═══════════════════════════════════════

  async startMic() {
    if (this.micActive) return;

    // Get mic stream with user-friendly error messages
    try {
      this._captureStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: { ideal: 16000 },
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
    } catch (err) {
      if (err.name === 'NotAllowedError') {
        throw new Error('Microphone access denied. Check your browser permissions (lock icon in the address bar).');
      } else if (err.name === 'NotFoundError') {
        throw new Error('No microphone detected. Please connect a microphone and try again.');
      } else if (err.name === 'NotReadableError') {
        throw new Error('Microphone is in use by another application. Close other apps using the mic and try again.');
      } else {
        throw new Error(`Microphone error: ${err.message}`);
      }
    }

    // Try to create AudioContext at 16kHz; fall back to default if unsupported
    this._captureContext = new AudioContext({ sampleRate: 16000 });
    if (this._captureContext.sampleRate !== 16000) {
      this._captureContext.close();
      this._captureContext = new AudioContext();
    }

    // AudioWorklet support check
    if (!this._captureContext.audioWorklet) {
      this.stopMic();
      throw new Error('Your browser doesn\'t support audio recording. Please use Chrome, Edge, or Firefox.');
    }

    const source = this._captureContext.createMediaStreamSource(this._captureStream);

    // Create AnalyserNode for waveform visualization
    this._micAnalyser = this._captureContext.createAnalyser();
    this._micAnalyser.fftSize = 256;
    this._micAnalyser.smoothingTimeConstant = 0.8;
    source.connect(this._micAnalyser);

    // Load and connect AudioWorklet
    await this._captureContext.audioWorklet.addModule('/static/pcm-processor.js');
    this._workletNode = new AudioWorkletNode(this._captureContext, 'pcm-processor');

    // Handle audio chunks from worklet
    this._workletNode.port.onmessage = (event) => {
      if (event.data.type === 'audio-chunk') {
        const { samples, sampleRate: sourceSampleRate } = event.data;
        this._processAudioChunk(samples, sourceSampleRate);
      }
    };

    source.connect(this._workletNode);
    // Don't connect worklet to destination — we don't want to hear our own mic

    this.micActive = true;
    console.log(`[MediaHandler] Mic started at ${this._captureContext.sampleRate}Hz`);
  }

  /**
   * Get the mic AnalyserNode for waveform visualization.
   */
  getMicAnalyser() {
    return this._micAnalyser;
  }

  /**
   * Get the playback AnalyserNode for waveform visualization.
   */
  getPlaybackAnalyser() {
    return this._playbackAnalyser;
  }

  _processAudioChunk(float32Samples, sourceSampleRate) {
    let samples = float32Samples;

    // Resample to 16kHz if needed
    if (sourceSampleRate !== 16000) {
      samples = this._downsample(float32Samples, sourceSampleRate, 16000);
    }

    // Convert Float32 [-1.0, 1.0] to Int16 [-32768, 32767] little-endian
    const pcm16 = this._float32ToInt16LE(samples);

    // Base64 encode and send
    const base64 = this._arrayBufferToBase64(pcm16.buffer);
    if (this._onAudioChunk) {
      this._onAudioChunk(base64);
    }
  }

  /**
   * Downsample Float32Array from sourceSampleRate to targetSampleRate.
   * Uses linear interpolation — simple but adequate for voice.
   */
  _downsample(buffer, sourceSampleRate, targetSampleRate) {
    if (sourceSampleRate === targetSampleRate) return buffer;
    const ratio = sourceSampleRate / targetSampleRate;
    const newLength = Math.floor(buffer.length / ratio);
    const result = new Float32Array(newLength);
    for (let i = 0; i < newLength; i++) {
      const srcIndex = i * ratio;
      const srcIndexFloor = Math.floor(srcIndex);
      const srcIndexCeil = Math.min(srcIndexFloor + 1, buffer.length - 1);
      const frac = srcIndex - srcIndexFloor;
      result[i] = buffer[srcIndexFloor] * (1 - frac) + buffer[srcIndexCeil] * frac;
    }
    return result;
  }

  /**
   * Convert Float32Array [-1.0, 1.0] to Int16 PCM little-endian.
   */
  _float32ToInt16LE(float32Array) {
    const int16 = new Int16Array(float32Array.length);
    for (let i = 0; i < float32Array.length; i++) {
      const s = Math.max(-1, Math.min(1, float32Array[i]));
      int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return int16;
  }

  stopMic() {
    if (this._workletNode) {
      this._workletNode.disconnect();
      this._workletNode = null;
    }
    this._micAnalyser = null;
    if (this._captureContext) {
      this._captureContext.close();
      this._captureContext = null;
    }
    if (this._captureStream) {
      this._captureStream.getTracks().forEach(t => t.stop());
      this._captureStream = null;
    }
    this.micActive = false;
    console.log('[MediaHandler] Mic stopped');
  }

  // ═══════════════════════════════════════
  // AUDIO PLAYBACK (24kHz PCM from Gemini)
  // ═══════════════════════════════════════

  _ensurePlaybackContext() {
    if (!this._playbackContext || this._playbackContext.state === 'closed') {
      try {
        this._playbackContext = new AudioContext({ sampleRate: 24000 });
      } catch {
        this._playbackContext = new AudioContext();
      }

      // Create AnalyserNode for playback waveform visualization
      this._playbackAnalyser = this._playbackContext.createAnalyser();
      this._playbackAnalyser.fftSize = 256;
      this._playbackAnalyser.smoothingTimeConstant = 0.8;
      this._playbackAnalyser.connect(this._playbackContext.destination);
    }

    // Resume context if suspended (autoplay policy)
    if (this._playbackContext.state === 'suspended') {
      this._playbackContext.resume();
    }
  }

  /**
   * Queue a base64-encoded PCM audio chunk for playback.
   * Data is 16-bit signed int, 24000 Hz, mono, little-endian.
   */
  playAudio(base64Data) {
    this._ensurePlaybackContext();

    // Decode base64 -> Int16 -> Float32
    const pcmBytes = this._base64ToArrayBuffer(base64Data);
    const int16 = new Int16Array(pcmBytes);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 0x8000;
    }

    this._playbackQueue.push(float32);
    this._schedulePlayback();
  }

  _schedulePlayback() {
    if (this._playbackQueue.length === 0) return;

    const ctx = this._playbackContext;
    const now = ctx.currentTime;

    // Schedule all queued chunks for gapless playback
    while (this._playbackQueue.length > 0) {
      const samples = this._playbackQueue.shift();
      const buffer = ctx.createBuffer(1, samples.length, 24000);
      buffer.getChannelData(0).set(samples);

      const source = ctx.createBufferSource();
      source.buffer = buffer;
      // Route through analyser for waveform visualization
      source.connect(this._playbackAnalyser);

      // Schedule: either now or immediately after the last scheduled chunk
      const startTime = Math.max(now, this._nextPlayTime);
      source.start(startTime);
      this._nextPlayTime = startTime + buffer.duration;

      this._currentSource = source;

      // Track when playback ends
      source.onended = () => {
        if (this._playbackQueue.length === 0 && this._onAudioStateChange) {
          this._isPlaying = false;
          this._onAudioStateChange('idle');
        }
      };
    }

    if (!this._isPlaying && this._onAudioStateChange) {
      this._isPlaying = true;
      this._onAudioStateChange('playing');
    }
  }

  /**
   * Stop all currently playing and queued audio.
   * Called when user interrupts (barge-in).
   */
  stopPlayback() {
    this._playbackQueue = [];
    this._nextPlayTime = 0;
    if (this._currentSource) {
      try { this._currentSource.stop(0); } catch {}
      this._currentSource = null;
    }
    this._isPlaying = false;
    if (this._onAudioStateChange) {
      this._onAudioStateChange('idle');
    }
  }

  // ═══════════════════════════════════════
  // VIDEO CAPTURE (Camera or Screen)
  // ═══════════════════════════════════════

  /**
   * Start camera capture at 1 FPS.
   * Returns the MediaStream so the UI can display a preview.
   */
  async startCamera() {
    if (this.videoActive) this.stopVideo();

    try {
      this._videoStream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 1280 },
          height: { ideal: 720 },
          facingMode: 'environment',
        },
        audio: false,
      });
    } catch (err) {
      if (err.name === 'NotAllowedError') {
        throw new Error('Camera access denied. You can still use screen share or text input.');
      } else if (err.name === 'NotFoundError') {
        throw new Error('No camera detected. You can use screen share instead.');
      } else if (err.name === 'NotReadableError') {
        throw new Error('Camera is in use by another application. Close other apps and try again.');
      } else {
        throw new Error(`Camera error: ${err.message}`);
      }
    }

    this.videoMode = 'camera';
    this._setupVideoCapture();
    console.log('[MediaHandler] Camera started');
    return this._videoStream;
  }

  /**
   * Start screen share capture at 1 FPS.
   * Returns the MediaStream so the UI can display a preview.
   */
  async startScreenShare() {
    if (this.videoActive) this.stopVideo();

    try {
      this._videoStream = await navigator.mediaDevices.getDisplayMedia({
        video: {
          width: { ideal: 1920 },
          height: { ideal: 1080 },
          frameRate: { ideal: 5 },
        },
        audio: false,
      });
    } catch (err) {
      // User cancelled the picker — not an error, re-throw as-is
      if (err.name === 'NotAllowedError') {
        throw err;
      }
      throw new Error(`Screen share error: ${err.message}`);
    }

    // Handle user clicking "Stop sharing" in the browser UI
    this._videoStream.getVideoTracks()[0].onended = () => {
      this.stopVideo();
    };

    this.videoMode = 'screen';
    this._setupVideoCapture();
    console.log('[MediaHandler] Screen share started');
    return this._videoStream;
  }

  _setupVideoCapture() {
    // Create hidden video element to read frames from
    this._videoElement = document.createElement('video');
    this._videoElement.srcObject = this._videoStream;
    this._videoElement.muted = true;
    this._videoElement.playsInline = true;
    this._videoElement.play();

    // Create canvas for frame extraction
    this._canvas = document.createElement('canvas');
    this._canvasCtx = this._canvas.getContext('2d');

    // Start 1 FPS capture loop
    this._frameInterval = setInterval(() => {
      this._captureFrame();
    }, 1000 / this._VIDEO_FPS);

    this.videoActive = true;
  }

  _captureFrame() {
    if (!this._videoElement || this._videoElement.readyState < 2) return;

    const video = this._videoElement;
    let width = video.videoWidth;
    let height = video.videoHeight;

    if (width === 0 || height === 0) return;

    // Scale down if needed to keep payload small
    if (width > this._VIDEO_MAX_WIDTH) {
      const scale = this._VIDEO_MAX_WIDTH / width;
      width = Math.floor(width * scale);
      height = Math.floor(height * scale);
    }

    this._canvas.width = width;
    this._canvas.height = height;
    this._canvasCtx.drawImage(video, 0, 0, width, height);

    // Export as JPEG base64
    const dataUrl = this._canvas.toDataURL('image/jpeg', this._VIDEO_QUALITY);
    const base64 = dataUrl.split(',')[1];

    if (this._onVideoFrame) {
      this._onVideoFrame(base64);
    }
  }

  /**
   * Get the current video stream for UI preview.
   */
  getVideoStream() {
    return this._videoStream;
  }

  stopVideo() {
    if (this._frameInterval) {
      clearInterval(this._frameInterval);
      this._frameInterval = null;
    }
    if (this._videoElement) {
      this._videoElement.pause();
      this._videoElement.srcObject = null;
      this._videoElement = null;
    }
    if (this._videoStream) {
      this._videoStream.getTracks().forEach(t => t.stop());
      this._videoStream = null;
    }
    this._canvas = null;
    this._canvasCtx = null;
    this.videoActive = false;
    this.videoMode = null;
    console.log('[MediaHandler] Video stopped');
  }

  // ═══════════════════════════════════════
  // UTILITIES
  // ═══════════════════════════════════════

  _arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  }

  _base64ToArrayBuffer(base64) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
  }

  stopAll() {
    this.stopMic();
    this.stopPlayback();
    this.stopVideo();
    console.log('[MediaHandler] All media stopped');
  }
}
