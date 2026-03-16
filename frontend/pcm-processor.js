/**
 * ORÁCULO PCM Audio Worklet Processor
 *
 * Runs on the audio rendering thread. Captures raw Float32 PCM samples
 * from the microphone input and sends them to the main thread in chunks.
 *
 * The main thread is responsible for:
 * - Resampling from AudioContext.sampleRate (typically 48000) to 16000 Hz
 * - Converting Float32 [-1.0, 1.0] to Int16 [-32768, 32767]
 * - Base64 encoding for WebSocket transport
 */
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(0);
    // Send chunks of ~100ms worth of samples at the context's sample rate.
    // At 48kHz, 100ms = 4800 samples. This balances latency vs overhead.
    this._chunkSize = 4800;
  }

  process(inputs, outputs, parameters) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;

    const channelData = input[0]; // Mono channel 0
    if (!channelData || channelData.length === 0) return true;

    // Append to buffer using typed arrays (avoid GC pressure from Array.push)
    const newBuffer = new Float32Array(this._buffer.length + channelData.length);
    newBuffer.set(this._buffer);
    newBuffer.set(channelData, this._buffer.length);
    this._buffer = newBuffer;

    // Send complete chunks
    while (this._buffer.length >= this._chunkSize) {
      const chunk = this._buffer.slice(0, this._chunkSize);
      this._buffer = this._buffer.slice(this._chunkSize);
      this.port.postMessage({
        type: 'audio-chunk',
        samples: chunk,
        sampleRate: sampleRate // globalThis.sampleRate in AudioWorklet scope
      });
    }

    return true; // Keep processor alive
  }
}

registerProcessor('pcm-processor', PCMProcessor);
