"""
ORÁCULO Gemini Live API Session Manager — v2.0 (Hardened)
==========================================================

Critical improvements over v1.0:
1. Context Window Compression — extends session from 2min to unlimited
2. Session Resumption — survives WebSocket resets without losing context
3. GoAway handling — graceful reconnection on server-initiated disconnect
4. Audio transcription — enables text in activity log
5. Proper async context manager lifecycle — no resource leaks
6. Structured error categorization — different handling for transient vs fatal

Reference: https://ai.google.dev/gemini-api/docs/live-api/best-practices
"""
import asyncio
import logging
import time as _time
from typing import Optional, Callable, Awaitable

from google import genai
from google.genai import types

from config import cfg
from tools import TOOL_DECLARATIONS, TOOL_FUNCTIONS

logger = logging.getLogger(__name__)

# ── System Prompt ──
SYSTEM_INSTRUCTION = """You are Oráculo, a senior market analyst with 20 years of experience at a top-tier quantitative trading firm. You are now serving as the personal market intelligence companion for a professional trader.

CORE CAPABILITIES:
- You can SEE the trader's screen in real-time via their camera or screen share. Analyze charts, candlestick patterns, support/resistance levels, trend lines, volume profiles, and options chains as they appear.
- You can HEAR the trader's voice and respond conversationally. Handle interruptions naturally — when interrupted, acknowledge briefly and pivot to the new question.
- You can PULL live market data using your tools when you need precise, current numbers.

ANALYSIS FRAMEWORK:
When analyzing what you see on screen, structure your thinking around:
1. Price Action: Current trend direction, key support/resistance levels, candlestick patterns (engulfing, doji, hammer, etc.)
2. Volume: Is volume confirming or diverging from the price move?
3. Technical Setup: What do the indicators suggest? (RSI overbought/oversold, MACD cross, SMA alignment)
4. Options Flow: If visible, interpret put/call ratios, unusual open interest, implied volatility levels
5. Risk Context: What could invalidate this setup? What levels should be watched?

COMMUNICATION RULES:
- Lead with the most actionable insight. Don't bury the lede.
- Use specific numbers and price levels, not vague statements like "it looks bullish."
- If you're not sure about something, say so explicitly. Never fabricate data.
- Keep responses concise during what seems like active trading — aim for 15-30 second responses. Elaborate only when asked or during calm periods.
- Never give explicit buy/sell recommendations. Frame everything as analysis and observations.
- When you use a tool to fetch data, briefly mention the source: "Looking at the current quote..." or "Pulling up the technicals..."
- You speak both English and Spanish fluently. Match whatever language the trader uses.

PERSONALITY:
- Confident but not arrogant. You've seen thousands of market cycles.
- Direct and data-driven. You respect the trader's time.
- Occasionally dry humor when appropriate — you're a colleague, not a robot.
- When you spot something interesting on screen proactively, interject naturally: "Hey, I notice that..." or "Worth noting..."

GROUNDING RULES:
- ALWAYS use your tools to fetch current data before making claims about specific prices, levels, or moves. Your visual interpretation of charts gives approximate levels — tools give exact numbers.
- Clearly distinguish between what you SEE on screen (visual analysis) and what your tools REPORT (quantitative data).
- If you can't see the screen clearly, say so and ask the user to adjust.
- Never make up ticker symbols, prices, or news headlines.

TOOL USAGE RULES:
- When the user asks for a specific price, quote, or "where is [ticker] trading":
  → ALWAYS call get_stock_quote. NEVER guess or use remembered prices.
- When the user asks "what's moving the market", "any news", "why is [ticker] moving":
  → Call get_market_news with the relevant ticker or topic.
- When the user asks about RSI, MACD, moving averages, "is it overbought/oversold",
  or asks you to confirm a technical pattern you see on screen:
  → Call get_technical_indicators.
- When the user asks about options, put/call ratio, max pain, open interest,
  or you see an options chain on their screen:
  → Call get_options_snapshot.
- When the user asks a general question, gives a greeting, or asks about
  concepts (like "explain RSI" or "what is max pain"):
  → Do NOT call any tools. Answer from your knowledge.
- When you see a chart on screen and want to provide analysis:
  → First describe what you SEE (visual analysis).
  → Then call the relevant tools to get exact numbers to confirm your visual read.
  → Synthesize both: "I can see what looks like a double bottom around the 575 level.
    Let me pull the exact technicals... [tool call] ...confirmed, RSI at 31 which
    supports the oversold reading I see on the chart."
- You can call multiple tools in sequence if the user's question requires it.
  For example, "Give me the full picture on NVDA" should trigger get_stock_quote,
  get_technical_indicators, and get_options_snapshot.
- After receiving tool results, ALWAYS incorporate the data into your spoken response.
  Never just acknowledge the tool call — tell the user what the data shows."""


class GeminiLiveSession:
    """
    Manages a single Gemini Live API session with:
    - Bidirectional audio/video streaming
    - Function calling with manual dispatch
    - Context window compression (unlimited session duration)
    - Session resumption (survives WebSocket resets)
    - GoAway handling (graceful server-initiated disconnect)
    """

    def __init__(self, callbacks: dict[str, Callable]):
        """
        callbacks dict keys:
          on_audio:           async (bytes) -> None
          on_text:            async (str) -> None
          on_transcript:      async (str, str) -> None  # (text, role: "user"|"model")
          on_interrupted:     async () -> None
          on_tool_call:       async (str) -> None  # tool name
          on_turn_complete:   async () -> None
          on_go_away:         async (float) -> None  # seconds until disconnect
          on_session_resumed: async () -> None
          on_error:           async (str) -> None
        """
        self._callbacks = callbacks
        self._client = genai.Client(api_key=cfg.GOOGLE_API_KEY)
        self._active = False
        self._session_task: Optional[asyncio.Task] = None

        # Queues for sending data TO Gemini (producer-consumer pattern)
        # Audio queue: maxsize=100 ≈ 3 seconds of buffered audio at 30ms chunks
        # Video queue: maxsize=5 = 5 frames (drop old when full)
        # Text queue: maxsize=10
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self._video_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=5)
        self._text_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=10)

        # Session resumption state — persists across reconnects
        self._resumption_handle: Optional[str] = None

        # Per-connection shutdown signal.
        # Set by _receive_loop (on exit) or any send loop (on ConnectionClosed).
        # All loops check this so the gather() completes even when queues are empty.
        self._session_stop: Optional[asyncio.Event] = None

    async def connect(self):
        """Start the session in a background task."""
        self._active = True
        self._session_task = asyncio.create_task(
            self._run_session(),
            name="gemini-live-session"
        )
        # Brief yield so the task starts before the caller proceeds
        await asyncio.sleep(0.1)
        logger.info(
            f"Gemini Live session launch initiated "
            f"(model={cfg.GEMINI_MODEL}, voice={cfg.GEMINI_VOICE}, "
            f"compression={'ON' if cfg.GEMINI_ENABLE_COMPRESSION else 'OFF'}, "
            f"resumption={'ON' if cfg.GEMINI_ENABLE_RESUMPTION else 'OFF'})"
        )

    async def _build_config(self) -> types.LiveConnectConfig:
        """Build the Live API session config with all hardening features."""
        config_kwargs = {
            "response_modalities": ["AUDIO"],
            "system_instruction": types.Content(
                parts=[types.Part(text=SYSTEM_INSTRUCTION)]
            ),
            "speech_config": types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=cfg.GEMINI_VOICE
                    )
                )
            ),
            "tools": [types.Tool(function_declarations=[
                types.FunctionDeclaration(**decl) for decl in TOOL_DECLARATIONS
            ])],
        }

        # ── CRITICAL FIX #1: Context Window Compression ──
        # Without this, audio+video sessions terminate after ~2 minutes.
        # Sliding window compresses older context to stay within token limits.
        if cfg.GEMINI_ENABLE_COMPRESSION:
            CWC = getattr(types, "ContextWindowCompressionConfig", None)
            SW = getattr(types, "SlidingWindow", None)
            if CWC and SW:
                config_kwargs["context_window_compression"] = CWC(sliding_window=SW())
                logger.debug("Context window compression: ENABLED")
            else:
                logger.warning(
                    "ContextWindowCompressionConfig not available in this SDK version — "
                    "sessions may terminate after ~2 minutes with video"
                )

        # ── Audio transcription for the activity log ──
        ATC = getattr(types, "AudioTranscriptionConfig", None)
        if ATC:
            config_kwargs["input_audio_transcription"] = ATC()
            config_kwargs["output_audio_transcription"] = ATC()
            logger.debug("Audio transcription: ENABLED")

        # ── CRITICAL FIX #2: Session Resumption ──
        if cfg.GEMINI_ENABLE_RESUMPTION:
            SRC = getattr(types, "SessionResumptionConfig", None)
            if SRC:
                resumption_kwargs = {}
                if self._resumption_handle:
                    resumption_kwargs["handle"] = self._resumption_handle
                config_kwargs["session_resumption"] = SRC(**resumption_kwargs)
                logger.debug(
                    f"Session resumption: ENABLED "
                    f"(handle={'set' if self._resumption_handle else 'new'})"
                )
            else:
                logger.warning(
                    "SessionResumptionConfig not available in this SDK version — "
                    "context will be lost on WebSocket reset"
                )

        return types.LiveConnectConfig(**config_kwargs)

    async def _run_session(self):
        """
        Main session lifecycle. Runs inside an async task.
        Uses the SDK's context manager properly to avoid resource leaks.
        Implements reconnection on GoAway or unexpected disconnect.
        """
        reconnect_attempts = 0
        max_reconnects = 3

        while self._active and reconnect_attempts <= max_reconnects:
            # Fresh stop-event per connection attempt so a previous closed
            # session doesn't immediately abort the new one.
            self._session_stop = asyncio.Event()
            try:
                config = await self._build_config()

                async with self._client.aio.live.connect(
                    model=cfg.GEMINI_MODEL,
                    config=config
                ) as session:
                    reconnect_attempts = 0  # Reset on successful connect
                    handle_preview = (
                        self._resumption_handle[:8] + "..."
                        if self._resumption_handle else "new"
                    )
                    logger.info(
                        f"Gemini session connected "
                        f"(model={cfg.GEMINI_MODEL}, voice={cfg.GEMINI_VOICE}, "
                        f"resumption={handle_preview})"
                    )

                    if self._resumption_handle:
                        cb = self._callbacks.get("on_session_resumed")
                        if cb:
                            await cb()

                    # Run send/receive loops concurrently inside the context manager.
                    # When any loop exits (e.g., receive loop on disconnect),
                    # asyncio.gather cancels the rest and context manager cleanup runs.
                    await asyncio.gather(
                        self._send_audio_loop(session),
                        self._send_video_loop(session),
                        self._send_text_loop(session),
                        self._receive_loop(session),
                    )

            except asyncio.CancelledError:
                logger.info("Session task cancelled")
                break
            except Exception as e:
                if not self._active:
                    break
                reconnect_attempts += 1
                logger.error(
                    f"Session error (attempt {reconnect_attempts}/{max_reconnects}): {e}"
                )
                cb = self._callbacks.get("on_error")
                if cb:
                    if reconnect_attempts <= max_reconnects:
                        await cb(
                            f"Connection lost. Reconnecting "
                            f"({reconnect_attempts}/{max_reconnects})..."
                        )
                    else:
                        await cb(
                            f"Unable to maintain AI connection after "
                            f"{max_reconnects} attempts."
                        )
                if reconnect_attempts <= max_reconnects and self._active:
                    backoff = min(2 ** reconnect_attempts, 10)
                    logger.info(f"Reconnecting in {backoff}s...")
                    await asyncio.sleep(backoff)

        logger.info("Session lifecycle ended")

    @staticmethod
    def _is_connection_closed(exc: Exception) -> bool:
        """Return True if the exception signals a closed WebSocket/gRPC stream."""
        return (
            "ConnectionClosed" in type(exc).__name__
            or "closed" in str(exc).lower()
            or "EOF" in str(exc)
        )

    async def _send_audio_loop(self, session):
        """Drain audio queue and send to Gemini."""
        _chunk_count = 0
        while self._active and not self._session_stop.is_set():
            try:
                audio_bytes = await asyncio.wait_for(
                    self._audio_queue.get(), timeout=1.0
                )
                _chunk_count += 1
                if _chunk_count % 50 == 1:
                    logger.info(
                        f"Sending audio chunk: {len(audio_bytes)} bytes "
                        f"(chunk #{_chunk_count})"
                    )
                await session.send_realtime_input(
                    audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
                )
            except asyncio.TimeoutError:
                continue  # re-checks _session_stop.is_set() on next iteration
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._is_connection_closed(e):
                    logger.warning("Audio send loop: session closed, exiting")
                    self._session_stop.set()  # wake up the other idle loops too
                    break
                if self._active:
                    logger.warning(f"Audio send error: {e}", exc_info=True)
        logger.debug("Audio send loop exited")

    async def _send_video_loop(self, session):
        """Drain video queue and send to Gemini."""
        while self._active and not self._session_stop.is_set():
            try:
                jpeg_bytes = await asyncio.wait_for(
                    self._video_queue.get(), timeout=1.0
                )
                await session.send_realtime_input(
                    video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._is_connection_closed(e):
                    logger.warning("Video send loop: session closed, exiting")
                    self._session_stop.set()
                    break
                if self._active:
                    logger.warning(f"Video send error: {e}", exc_info=True)
        logger.debug("Video send loop exited")

    async def _send_text_loop(self, session):
        """Drain text queue and send to Gemini."""
        while self._active and not self._session_stop.is_set():
            try:
                text = await asyncio.wait_for(
                    self._text_queue.get(), timeout=1.0
                )
                logger.info(f"Sending text: {text[:120]!r}")
                # Use send_realtime_input(text=) instead of send_client_content().
                # Once audio is streaming, the session is in "realtime" mode.
                # send_client_content() creates a structured turn that can stall
                # in an active streaming session — Gemini receives it but doesn't
                # generate a response. send_realtime_input() is consistent with
                # how audio/video are sent and reliably triggers generation.
                await session.send_realtime_input(text=text)
                logger.info("Text delivered via send_realtime_input")
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._is_connection_closed(e):
                    logger.warning("Text send loop: session closed, exiting")
                    self._session_stop.set()
                    break
                if self._active:
                    logger.warning(f"Text send error: {e}", exc_info=True)
        logger.debug("Text send loop exited")

    async def _receive_loop(self, session):
        """
        Process all messages from Gemini.
        Dispatches to typed handlers for clean separation of concerns.
        """
        try:
            logger.info("Receive loop: starting — waiting for first message from Gemini")
            async for response in session.receive():
                if not self._active:
                    break

                # ── Diagnostic: log every response envelope ──
                logger.info(
                    f"Receive loop got: "
                    f"data={response.data is not None}, "
                    f"text={response.text is not None}, "
                    f"tool_call={response.tool_call is not None}, "
                    f"server_content={response.server_content is not None}"
                )

                try:
                    # Audio data
                    if response.data is not None:
                        cb = self._callbacks.get("on_audio")
                        if cb:
                            await cb(response.data)

                    # Text content
                    if response.text is not None:
                        cb = self._callbacks.get("on_text")
                        if cb:
                            await cb(response.text)

                    # Function calls
                    if response.tool_call:
                        await self._handle_tool_calls(session, response.tool_call)

                    # Server content (turn_complete, interrupted, transcriptions)
                    if response.server_content:
                        sc = response.server_content

                        if sc.interrupted:
                            logger.info("Generation interrupted by user")
                            cb = self._callbacks.get("on_interrupted")
                            if cb:
                                await cb()

                        if sc.turn_complete:
                            cb = self._callbacks.get("on_turn_complete")
                            if cb:
                                await cb()

                        # ── Audio transcriptions ──
                        input_transcript = getattr(sc, "input_transcription", None)
                        if input_transcript and getattr(input_transcript, "text", None):
                            cb = self._callbacks.get("on_transcript")
                            if cb:
                                await cb(input_transcript.text, "user")

                        output_transcript = getattr(sc, "output_transcription", None)
                        if output_transcript and getattr(output_transcript, "text", None):
                            cb = self._callbacks.get("on_transcript")
                            if cb:
                                await cb(output_transcript.text, "model")

                    # ── CRITICAL FIX #2: Session Resumption Updates ──
                    resumption_update = getattr(response, "session_resumption_update", None)
                    if resumption_update:
                        resumable = getattr(resumption_update, "resumable", False)
                        new_handle = getattr(resumption_update, "new_handle", None)
                        if resumable and new_handle:
                            self._resumption_handle = new_handle
                            logger.debug(
                                f"Session resumption handle updated: "
                                f"{self._resumption_handle[:16]}..."
                            )

                    # ── CRITICAL FIX #3: GoAway Handling ──
                    # Server sends this before terminating. We capture the handle
                    # (already stored above) and let _run_session reconnect.
                    go_away = getattr(response, "go_away", None)
                    if go_away:
                        time_left = getattr(go_away, "time_left", 0)
                        logger.warning(
                            f"GoAway received — {time_left}s until server disconnect"
                        )
                        cb = self._callbacks.get("on_go_away")
                        if cb:
                            await cb(float(time_left) if time_left else 0.0)
                        # Break receive loop — _run_session will reconnect with handle
                        break

                except Exception as e:
                    logger.error(f"Error processing Gemini message: {e}", exc_info=True)

                logger.info("Receive loop: waiting for next message...")

            # If we reach here, the async for exhausted normally (stream closed
            # server-side without a GoAway). This should not happen in a healthy
            # session — seeing this log means the Gemini connection dropped silently.
            logger.warning("Receive loop: async for ended — session stream closed")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self._active:
                logger.error(f"Receive loop error: {e}", exc_info=True)
        finally:
            # Always signal send loops to exit so asyncio.gather() can complete.
            # Without this, send loops with empty queues spin on 1-second timeouts
            # forever and gather() never returns, blocking the reconnect.
            self._session_stop.set()
            logger.info("Receive loop ended")

    async def _handle_tool_calls(self, session, tool_call):
        """Execute function calls and return results to Gemini."""
        function_responses = []

        for fc in tool_call.function_calls:
            tool_name = fc.name
            tool_args = fc.args or {}

            logger.info(f"Tool call: {tool_name}({tool_args})")

            # Notify frontend
            cb = self._callbacks.get("on_tool_call")
            if cb:
                await cb(tool_name)

            # Execute tool
            start = _time.monotonic()
            tool_fn = TOOL_FUNCTIONS.get(tool_name)
            if tool_fn:
                try:
                    sanitized_args = {
                        k: self._sanitize_tool_arg(v) for k, v in tool_args.items()
                    }
                    result = await tool_fn(**sanitized_args)
                except Exception as e:
                    logger.error(f"Tool execution error ({tool_name}): {e}")
                    result = {"error": "Data temporarily unavailable. Try asking again."}
            else:
                logger.warning(f"Unknown tool requested: {tool_name}")
                result = {"error": f"Unknown tool: {tool_name}"}

            duration_ms = (_time.monotonic() - start) * 1000
            logger.info(f"Tool {tool_name} completed in {duration_ms:.0f}ms")

            function_responses.append(
                types.FunctionResponse(
                    id=fc.id,
                    name=tool_name,
                    response=result
                )
            )

        await session.send_tool_response(function_responses=function_responses)
        logger.info(f"Sent {len(function_responses)} tool response(s)")

    @staticmethod
    def _sanitize_tool_arg(value) -> str:
        """
        Sanitize tool arguments to prevent injection.
        Tool args come from Gemini's interpretation of user speech —
        they should be simple strings, but we validate anyway.
        """
        if isinstance(value, str):
            # Strip whitespace and limit length
            sanitized = value.strip()[:50]
            # Allow alphanumeric, spaces, dots, commas, hyphens only
            sanitized = "".join(c for c in sanitized if c.isalnum() or c in ".-, ")
            return sanitized
        elif isinstance(value, (int, float)):
            return value
        else:
            return str(value)[:50]

    # ── Public API (called by main.py) ──

    async def send_audio(self, audio_bytes: bytes):
        """Send PCM audio chunk (16kHz, 16-bit, mono, little-endian)."""
        if self._active:
            try:
                self._audio_queue.put_nowait(audio_bytes)
            except asyncio.QueueFull:
                pass  # Drop frames rather than blocking — correct for real-time audio

    async def send_video_frame(self, jpeg_bytes: bytes):
        """Send a JPEG video frame. Drops oldest frame if queue is full."""
        if self._active:
            try:
                self._video_queue.put_nowait(jpeg_bytes)
            except asyncio.QueueFull:
                # Drop old frame, insert new one — always have latest visual
                try:
                    self._video_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    self._video_queue.put_nowait(jpeg_bytes)
                except asyncio.QueueFull:
                    pass

    async def send_text(self, text: str):
        """Send a text message."""
        if self._active:
            try:
                self._text_queue.put_nowait(text)
            except asyncio.QueueFull:
                pass

    async def close(self):
        """Clean up the session."""
        self._active = False
        if self._session_task:
            self._session_task.cancel()
            try:
                await asyncio.wait_for(self._session_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        logger.info("Gemini Live session closed")
