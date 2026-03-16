# ORÁCULO — Project Context for Claude Code

ORÁCULO is a real-time voice + vision market intelligence agent built for the Gemini Live Agent Challenge hackathon (deadline: March 16, 2026). It lets traders talk to an AI analyst that simultaneously sees their trading screens via camera/screen-share and responds with spoken analysis, pulling live market data through function calling.

## Tech Stack

- **Backend**: Python 3.11, FastAPI, uvicorn, WebSockets
- **AI**: Google GenAI SDK (`google-genai`) — Gemini 2.5 Flash Live API
- **Frontend**: Vanilla HTML/CSS/JS (no build step), Web Audio API, AudioWorklet
- **Cloud**: Google Cloud Run, Cloud Firestore, Artifact Registry, Cloud Build
- **Market Data**: Alpha Vantage API, Yahoo Finance (yfinance)

## Key Architecture Decisions

- Uses `google-genai` (the NEW SDK), NOT `google-generativeai`. Import: `from google import genai`
- Gemini Live API does NOT support automatic function calling — must handle `response.tool_call` manually, execute the function, and call `session.send_tool_response()`
- Audio input to Gemini: PCM 16-bit signed LE, 16000 Hz, mono
- Audio output from Gemini: PCM 16-bit signed LE, 24000 Hz, mono
- Browser AudioContext is typically 48kHz — frontend downsamples to 16kHz for input, plays 24kHz for output
- Video: 1 FPS JPEG frames max (API processes at 1 FPS regardless)
- WebSocket JSON protocol between browser and backend; GenAI SDK handles Gemini binary protocol
- Firestore must degrade gracefully when unavailable (local dev without GCP)
- Session management uses `async with client.aio.live.connect()` with an input queue pattern since the SDK requires context manager usage

## Running Locally

```bash
pip install -r requirements.txt
cp .env.example .env  # add your GOOGLE_API_KEY
uvicorn main:app --port 8080
```

Open http://localhost:8080

## Testing

1. Click "Start Session" — WebSocket connects, Oráculo speaks greeting
2. Click mic button — start talking, audio streams to Gemini
3. Click screen button — share a trading chart, ask "What do you see?"
4. Ask "What's SPY at?" — triggers `get_stock_quote` tool call
5. Interrupt while Oráculo is speaking — should stop and listen

## Known Constraints

- Gemini Live API has a ~10 minute session limit
- Video is processed at 1 FPS regardless of send rate
- Function calling is manual (not automatic) in the Live API
- Cloud Run WebSocket timeout must be set to 3600s for longer sessions

## File Map

- `main.py` — FastAPI server + WebSocket endpoint (bridges browser <-> Gemini)
- `gemini_live.py` — Gemini Live API session wrapper (core streaming logic)
- `tools.py` — 4 function calling tools (stock quote, news, technicals, options)
- `config.py` — Environment config with dotenv
- `firestore_utils.py` — Session logging to Cloud Firestore
- `frontend/index.html` — Main UI (dark trading terminal)
- `frontend/main.js` — App orchestration + state machine
- `frontend/websocket-client.js` — WebSocket connection with auto-reconnect
- `frontend/media-handler.js` — Audio capture/playback + video capture
- `frontend/pcm-processor.js` — AudioWorklet for PCM encoding
- `frontend/styles.css` — Dark theme styling

## Reference Docs

- Gemini Live API: https://ai.google.dev/gemini-api/docs/live
- Gemini Live Tools: https://ai.google.dev/gemini-api/docs/live-tools
- Google GenAI SDK: https://ai.google.dev/gemini-api/docs/sdks
