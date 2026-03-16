<div align="center">

# 🔮 ORÁCULO

### Real-Time Voice + Vision Market Intelligence Agent

*What if every retail trader had a senior market analyst sitting next to them,
watching their screens and answering questions in real-time?*

**Gemini Live Agent Challenge** · Live Agents Category 🗣️

Built by [edd clandestino](https://github.com/eddclandestino) · San Juan, PR

[![Deploy to Cloud Run](https://deploy.cloud.run/button.svg)](https://deploy.cloud.run)

</div>

---

## 🎯 The Problem

Institutional trading desks employ teams of analysts who watch multiple screens, interpret options flow, and communicate insights verbally in real-time. Retail traders get... a text box.

Current AI trading assistants require typed input (breaking workflow), return static text (requiring reading time), and cannot see what the trader is looking at (requiring manual context description). **The interaction model is fundamentally broken for a domain that demands real-time, hands-free intelligence.**

## 💡 The Solution

ORÁCULO is a real-time voice-and-vision market intelligence agent. It uses Google's Gemini Live API to create a bidirectional audio/video stream between a trader and an AI analyst that:

- **👁️ Sees** your trading screen via camera or screen share — interprets charts, candlestick patterns, support/resistance levels, and options chains in real-time
- **👂 Hears** your voice questions naturally — handles interruptions, follow-ups, and topic changes like a real conversation
- **🗣️ Speaks** institutional-grade analysis back to you — specific price levels, risk context, and actionable observations
- **📊 Pulls live data** via function calling — real-time quotes, technical indicators, market news, and options snapshots on demand

## ✨ Features

| Feature | Description |
|---------|-------------|
| **Natural Voice Interaction** | Talk to Oráculo like a colleague on the trading desk. Interrupt mid-sentence to change topics. |
| **Real-Time Screen Analysis** | Share your screen or camera. Oráculo identifies chart patterns, reads price levels, and spots trend changes. |
| **Live Market Data Tools** | Four function-calling tools pull real-time data: stock quotes, market news, technical indicators (RSI, MACD, Bollinger Bands), and options snapshots (P/C ratio, max pain, top OI strikes). |
| **Institutional Persona** | System prompt encodes 20 years of trading desk experience — GEX awareness, dealer positioning concepts, volume profile interpretation. Not a generic chatbot. |
| **Audio Waveform Visualization** | Real-time frequency visualization — blue when you speak, gold when Oráculo responds. |
| **Sub-Second Latency** | Gemini Live API processes continuous streams for natural conversation flow. |
| **Graceful Interruption** | Barge-in support — speak while Oráculo is talking and it stops immediately, acknowledges, and pivots. |

## 🏗️ Architecture

<div align="center">
<img src="docs/architecture-diagram.png" alt="ORÁCULO Architecture" width="800">
</div>

```
Browser (Audio 16kHz + Video 1FPS)
    ↕ WebSocket (JSON + Base64)
FastAPI Backend (Cloud Run)
    ├── Gemini Live API (audio/video processing + voice generation)
    ├── Function Tools → Alpha Vantage API + Yahoo Finance
    └── Cloud Firestore (session logging)
```

**Data Flow:**
1. Browser captures mic audio (16kHz PCM) and screen/camera frames (1 FPS JPEG)
2. Media streams via WebSocket to FastAPI backend on Cloud Run
3. Backend relays to Gemini Live API which processes audio + video simultaneously
4. When Gemini needs market data, it triggers function calls → backend executes → results returned
5. Gemini generates spoken response (24kHz PCM) incorporating visual analysis + tool data
6. Audio streams back through WebSocket → browser playback

## 🛠️ Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **AI Model** | Gemini 2.5 Flash (Live API) | Real-time multimodal processing + voice generation |
| **Agent SDK** | Google GenAI SDK (`google-genai`) | Live API session management + function calling |
| **Backend** | Python 3.11 · FastAPI · uvicorn | WebSocket server, tool execution, session orchestration |
| **Frontend** | Vanilla JS · Web Audio API · AudioWorklet | Audio capture/playback, video capture, waveform visualization |
| **Deployment** | Google Cloud Run | Serverless containers with WebSocket support |
| **Database** | Cloud Firestore | Session metadata + tool usage logging |
| **CI/CD** | Cloud Build · Artifact Registry | Automated Docker build → push → deploy pipeline |
| **Market Data** | Alpha Vantage API · Yahoo Finance (yfinance) | Real-time quotes, news, technicals, options chains |

## 🚀 Quick Start

### Prerequisites

- Python 3.11+ (or Docker)
- Google Cloud project with billing enabled
- Gemini API key ([get one free](https://aistudio.google.com/apikey))
- Alpha Vantage API key ([free signup](https://www.alphavantage.co/support/#api-key))

### Option A: Run Locally (Fastest)

```bash
# Clone the repo
git clone https://github.com/eddclandestino/oraculo.git
cd oraculo

# Set up environment
cp .env.example .env
# Edit .env and fill in your API keys:
#   GOOGLE_API_KEY=your-gemini-api-key
#   ALPHA_VANTAGE_API_KEY=your-alpha-vantage-key

# Install dependencies
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Start the server
uvicorn main:app --port 8080

# Open in browser
open http://localhost:8080   # or navigate manually
```

### Option B: Run with Docker

```bash
git clone https://github.com/eddclandestino/oraculo.git
cd oraculo
cp .env.example .env
# Edit .env with your API keys

docker build -t oraculo .
docker run -p 8080:8080 --env-file .env oraculo
```

### Option C: Run with Docker Compose

```bash
git clone https://github.com/eddclandestino/oraculo.git
cd oraculo
cp .env.example .env
# Edit .env with your API keys

docker compose up --build
```

### Using ORÁCULO

1. Open http://localhost:8080 in Chrome (recommended)
2. Click **▶ Start Session** — WebSocket connects, Oráculo greets you
3. Click **🎙️ Mic** — grant microphone permission
4. **Speak naturally**: "Hey Oráculo, what's SPY trading at?"
5. Click **🖥️ Screen** — share your trading screen (TradingView, ToS, etc.)
6. **Ask about what you see**: "What do you think about this chart?"
7. **Interrupt anytime** — just start speaking while Oráculo is talking

### Example Queries

| What to say | What happens |
|-------------|-------------|
| "What's Apple trading at?" | Calls `get_stock_quote(AAPL)` → speaks current price, change, volume |
| "Is NVDA overbought?" | Calls `get_technical_indicators(NVDA)` → RSI, MACD, Bollinger position |
| "What's the put/call ratio on SPY?" | Calls `get_options_snapshot(SPY)` → P/C ratio, max pain, top strikes |
| "Why is the market dropping?" | Calls `get_market_news(market)` → latest headlines with sentiment |
| "What do you see on my screen?" | Analyzes the shared screen/camera feed visually |
| "Give me the full picture on TSLA" | Chains multiple tool calls: quote + technicals + options |

## ☁️ Deploy to Google Cloud

### One-Time Setup

```bash
# Authenticate with Google Cloud
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Run the setup script (enables APIs, creates resources, stores secrets)
chmod +x scripts/setup_gcp.sh
./scripts/setup_gcp.sh
```

### Deploy

```bash
# Option 1: Automated via Cloud Build
gcloud builds submit --config cloudbuild.yaml

# Option 2: Manual deploy
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```

The service URL will be printed after deployment. Open it in your browser.

### Google Cloud Services Used

| Service | Purpose |
|---------|---------|
| **Cloud Run** | Hosts the FastAPI backend with WebSocket support |
| **Artifact Registry** | Stores Docker container images |
| **Cloud Build** | CI/CD pipeline (build → push → deploy) |
| **Cloud Firestore** | Session metadata and tool usage logging |
| **Secret Manager** | Securely stores API keys |
| **Vertex AI** | Gemini Live API access |

## 📁 Project Structure

```
oraculo/
├── main.py                  # FastAPI server + WebSocket endpoint
├── gemini_live.py           # Gemini Live API session wrapper
├── tools.py                 # 4 function calling tools (market data)
├── firestore_utils.py       # Session logging to Cloud Firestore
├── config.py                # Environment configuration
├── frontend/
│   ├── index.html           # Trading terminal UI
│   ├── main.js              # App state machine + orchestration
│   ├── media-handler.js     # Audio/video capture + playback
│   ├── websocket-client.js  # WebSocket connection manager
│   ├── pcm-processor.js     # AudioWorklet for PCM encoding
│   └── styles.css           # Dark theme styling
├── scripts/
│   ├── setup_gcp.sh         # One-time GCP project setup
│   ├── deploy.sh            # Quick manual deploy
│   └── generate_architecture_diagram.py
├── docs/
│   ├── architecture-diagram.svg
│   └── architecture-diagram.png
├── Dockerfile               # Multi-stage production build
├── cloudbuild.yaml          # Cloud Build CI/CD pipeline
├── docker-compose.yml       # Local dev convenience
├── requirements.txt
├── .env.example
└── CLAUDE.md                # Dev context for AI-assisted coding
```

## 🔧 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_API_KEY` | Yes | Gemini API key from [AI Studio](https://aistudio.google.com/apikey) |
| `ALPHA_VANTAGE_API_KEY` | Yes | Free key from [Alpha Vantage](https://www.alphavantage.co/support/#api-key) |
| `GOOGLE_CLOUD_PROJECT` | For deploy | GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | For deploy | GCP region (default: `us-central1`) |
| `GEMINI_MODEL` | No | Model name (default: `gemini-2.5-flash-native-audio-preview-12-2025`) |
| `GEMINI_VOICE` | No | Voice name (default: `Orus`). Options: Puck, Charon, Kore, Fenrir, Aoede, Leda, Orus, Zephyr |
| `PORT` | No | Server port (default: `8080`) |
| `ENVIRONMENT` | No | `development` or `production` |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## 📋 Hackathon Submission Checklist

- [x] Leverages a Gemini model (Gemini 2.5 Flash via Live API)
- [x] Built with Google GenAI SDK
- [x] Uses Google Cloud services (Cloud Run, Firestore, Artifact Registry, Cloud Build, Secret Manager)
- [x] Public code repository with spin-up instructions
- [x] Architecture diagram
- [x] Demo video (< 4 minutes)
- [x] GCP deployment proof
- [x] Blog post (#GeminiLiveAgentChallenge)
- [x] Automated cloud deployment (cloudbuild.yaml)
- [x] GDG membership

## 🔍 Findings & Learnings

1. **Gemini Live API function calling is manual** — unlike the standard API, the Live API doesn't support automatic function calling. You must listen for `tool_call` messages, execute functions yourself, and send `FunctionResponse` objects back. This adds complexity but gives full control over execution.

2. **Audio sample rate mismatch matters** — Gemini expects 16kHz PCM input but outputs 24kHz PCM. Browsers typically run AudioContext at 48kHz. Getting the resampling right (without artifacts) required careful AudioWorklet + linear interpolation implementation.

3. **Parallel tool fetches are critical for voice UX** — Technical indicators require 7 separate Alpha Vantage API calls. Sequential execution = 1.5s of dead air. Parallel via `asyncio.gather` = ~300ms. In a voice conversation, this difference is the difference between natural and awkward.

4. **Voice-friendly data formatting belongs in the tool, not the prompt** — When tools return raw numbers like `72.3456`, Gemini speaks every decimal. Pre-formatting to `"72.3"` with interpretation labels like `"Overbought — could see pullback"` gives the AI the exact words to speak.

5. **Max pain calculation provides real analytical edge** — Most hackathon submissions will use basic price data. Including options-level analysis (put/call ratio, max pain, GEX context in the system prompt) demonstrates domain depth that judges notice.

## 📄 License

Apache 2.0

## 🙏 Acknowledgments

- **Google Gemini Live API** — Real-time multimodal processing
- **Google Cloud** — Cloud Run, Firestore, Cloud Build infrastructure
- **Alpha Vantage** — Real-time market data APIs
- **Yahoo Finance (yfinance)** — Options chain data

---

<div align="center">

**ORÁCULO** — Your analyst is always watching.

Built with ☕ in San Juan, PR for the [Gemini Live Agent Challenge](https://geminiliveagentchallenge.devpost.com/)

\#GeminiLiveAgentChallenge

</div>
