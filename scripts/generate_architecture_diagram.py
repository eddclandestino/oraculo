"""
ORÁCULO Architecture Diagram Generator
Creates a publication-quality SVG architecture diagram.
Run: python scripts/generate_architecture_diagram.py
Output: docs/architecture-diagram.svg (and .png if cairosvg is available)
"""
import os

# ── Configuration ──
WIDTH = 1200
HEIGHT = 700
BG = "#0c1018"
GOLD = "#d4a847"
BLUE = "#3b82f6"
WHITE = "#e8ecf1"
MUTED = "#6b7a8d"
BORDER = "#1e2a3a"
SURFACE = "#141a26"
ELEVATED = "#1a2235"

def svg_rect(x, y, w, h, fill=SURFACE, stroke=BORDER, rx=8, opacity=1):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="1.5" opacity="{opacity}"/>'

def svg_text(x, y, text, fill=WHITE, size=13, weight="normal", anchor="start", font="'DM Sans', sans-serif"):
    return f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" font-family="{font}">{text}</text>'

def svg_mono_text(x, y, text, fill=MUTED, size=11):
    return f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" font-family="\'JetBrains Mono\', monospace">{text}</text>'

def svg_arrow(x1, y1, x2, y2, color=BLUE, dashed=False):
    dash = ' stroke-dasharray="6,4"' if dashed else ''
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="2" marker-end="url(#arrow-{color.replace("#","")})"  {dash}/>'

def svg_arrow_path(d, color=BLUE, dashed=False):
    dash = ' stroke-dasharray="6,4"' if dashed else ''
    return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" marker-end="url(#arrow-{color.replace("#","")})"  {dash}/>'

def arrow_marker(color):
    cid = color.replace("#", "")
    return f'''<marker id="arrow-{cid}" viewBox="0 0 10 7" refX="9" refY="3.5" markerWidth="8" markerHeight="6" orient="auto-start-reverse">
      <polygon points="0 0, 10 3.5, 0 7" fill="{color}"/>
    </marker>'''

def generate_svg():
    elements = []

    # ── Background ──
    elements.append(f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{BG}"/>')

    # ── Defs: Arrow markers + shadow filter ──
    elements.append('<defs>')
    elements.append(arrow_marker(BLUE))
    elements.append(arrow_marker(GOLD))
    elements.append(arrow_marker(MUTED))
    elements.append('''<filter id="shadow" x="-5%" y="-5%" width="110%" height="110%">
      <feDropShadow dx="0" dy="2" stdDeviation="4" flood-color="#000" flood-opacity="0.3"/>
    </filter>''')
    elements.append('</defs>')

    # ── Title ──
    elements.append(svg_text(WIDTH/2, 35, "ORÁCULO — System Architecture", GOLD, 20, "bold", "middle", "'JetBrains Mono', monospace"))
    elements.append(svg_text(WIDTH/2, 55, "Gemini Live Agent Challenge  |  Clandestino Ventures", MUTED, 11, "normal", "middle"))

    # ══════════════════════════════════════
    # BROWSER BOX (left side)
    # ══════════════════════════════════════
    bx, by, bw, bh = 30, 80, 200, 520
    elements.append(svg_rect(bx, by, bw, bh, fill=ELEVATED, stroke=BORDER))
    elements.append(svg_text(bx + bw/2, by + 25, "BROWSER", WHITE, 14, "bold", "middle"))
    elements.append(svg_text(bx + bw/2, by + 42, "Client", MUTED, 10, "normal", "middle"))

    # Browser sub-components
    browser_items = [
        ("Audio Capture", "16kHz PCM", 75),
        ("AudioWorklet", "Downsample + Encode", 145),
        ("Video / Screen", "1 FPS JPEG", 215),
        ("Audio Playback", "24kHz PCM", 285),
        ("Waveform Viz", "Canvas + AnalyserNode", 355),
        ("State Machine", "7 UI States", 425),
    ]
    for label, sub, offset in browser_items:
        ix, iy = bx + 15, by + offset
        elements.append(svg_rect(ix, iy, bw - 30, 50, fill=SURFACE, stroke=BORDER, rx=5))
        elements.append(svg_text(ix + (bw-30)/2, iy + 22, label, WHITE, 11, "600", "middle"))
        elements.append(svg_mono_text(ix + (bw-30)/2 - len(sub)*3, iy + 38, sub, MUTED, 9))

    # ══════════════════════════════════════
    # GOOGLE CLOUD BOX (right side)
    # ══════════════════════════════════════
    gcx, gcy, gcw, gch = 310, 80, 860, 520
    elements.append(svg_rect(gcx, gcy, gcw, gch, fill="#0a0e15", stroke=BORDER, rx=10))
    elements.append(svg_text(gcx + gcw/2, gcy + 25, "GOOGLE CLOUD", WHITE, 14, "bold", "middle"))

    # ── Cloud Run Container ──
    crx, cry, crw, crh = 350, 130, 240, 320
    elements.append(svg_rect(crx, cry, crw, crh, fill=ELEVATED, stroke=BORDER))
    elements.append(svg_text(crx + crw/2, cry + 22, "Cloud Run", WHITE, 13, "bold", "middle"))
    elements.append(svg_mono_text(crx + 20, cry + 40, "Single container instance", MUTED, 9))

    # Sub-components inside Cloud Run
    cr_items = [
        ("FastAPI Server", "WebSocket Endpoint", 55),
        ("GenAI SDK", "Live API Client", 120),
        ("Tools Engine", "Function Calling", 185),
        ("Session Logger", "Firestore Writer", 250),
    ]
    for label, sub, offset in cr_items:
        ix, iy = crx + 15, cry + offset
        elements.append(svg_rect(ix, iy, crw - 30, 50, fill=SURFACE, stroke=BORDER, rx=5))
        elements.append(svg_text(ix + (crw-30)/2, iy + 22, label, WHITE, 11, "600", "middle"))
        elements.append(svg_mono_text(ix + 10, iy + 38, sub, MUTED, 9))

    # ── Gemini Live API Box ──
    glx, gly, glw, glh = 650, 130, 240, 180
    elements.append(svg_rect(glx, gly, glw, glh, fill=ELEVATED, stroke=GOLD, rx=8))
    elements.append(svg_text(glx + glw/2, gly + 22, "Gemini Live API", GOLD, 13, "bold", "middle"))
    elements.append(svg_mono_text(glx + 15, gly + 45, "2.5 Flash (Native Audio)", MUTED, 9))

    gl_features = [
        "Audio Processing",
        "Vision Analysis (1 FPS)",
        "Voice Generation (24kHz)",
        "Function Calling (manual)",
    ]
    for i, feat in enumerate(gl_features):
        elements.append(svg_mono_text(glx + 20, gly + 68 + i * 22, f"• {feat}", WHITE, 10))

    # ── Function Tools Box ──
    ftx, fty, ftw, fth = 650, 340, 240, 140
    elements.append(svg_rect(ftx, fty, ftw, fth, fill=ELEVATED, stroke=BORDER, rx=8))
    elements.append(svg_text(ftx + ftw/2, fty + 22, "Function Tools", WHITE, 13, "bold", "middle"))

    tools = [
        "get_stock_quote",
        "get_market_news",
        "get_technical_indicators",
        "get_options_snapshot",
    ]
    for i, tool in enumerate(tools):
        elements.append(svg_mono_text(ftx + 20, fty + 48 + i * 22, f"• {tool}", "#22c55e", 10))

    # ── Firestore Box ──
    fsx, fsy, fsw, fsh = 350, 490, 180, 70
    elements.append(svg_rect(fsx, fsy, fsw, fsh, fill=SURFACE, stroke=BORDER, rx=8))
    elements.append(svg_text(fsx + fsw/2, fsy + 28, "Cloud Firestore", WHITE, 12, "600", "middle"))
    elements.append(svg_mono_text(fsx + 20, fsy + 48, "Session Metadata", MUTED, 9))

    # ── External APIs Box ──
    eax, eay, eaw, eah = 950, 340, 190, 140
    elements.append(svg_rect(eax, eay, eaw, eah, fill=SURFACE, stroke=BORDER, rx=8))
    elements.append(svg_text(eax + eaw/2, eay + 22, "External APIs", WHITE, 12, "600", "middle"))
    ext_apis = ["Alpha Vantage", "Yahoo Finance (yfinance)"]
    for i, api in enumerate(ext_apis):
        elements.append(svg_mono_text(eax + 15, eay + 50 + i * 22, f"• {api}", MUTED, 10))

    # ══════════════════════════════════════
    # ARROWS / CONNECTIONS
    # ══════════════════════════════════════

    # Browser <-> Cloud Run (WebSocket)
    elements.append(svg_arrow(bx + bw, by + 160, crx, cry + 75, GOLD))
    elements.append(svg_arrow(crx, cry + 95, bx + bw, by + 320, GOLD))
    # Label: WSS
    elements.append(svg_text(255, by + 145, "WSS", GOLD, 10, "bold", "middle", "'JetBrains Mono', monospace"))
    elements.append(svg_mono_text(240, by + 160, "JSON + base64", GOLD, 8))
    elements.append(svg_mono_text(240, by + 310, "PCM Audio", GOLD, 8))

    # Cloud Run -> Gemini Live API
    elements.append(svg_arrow(crx + crw, cry + 90, glx, gly + 70, BLUE))
    elements.append(svg_arrow(glx, gly + 100, crx + crw, cry + 120, BLUE))
    # Label
    elements.append(svg_text(605, cry + 78, "GenAI SDK", BLUE, 9, "bold", "middle", "'JetBrains Mono', monospace"))
    elements.append(svg_text(605, cry + 128, "Bidirectional", BLUE, 9, "normal", "middle", "'JetBrains Mono', monospace"))

    # Cloud Run Tools -> Function Tools
    elements.append(svg_arrow(crx + crw, cry + 210, ftx, fty + 50, BLUE))
    # Label
    elements.append(svg_text(605, cry + 205, "Execute", MUTED, 9, "normal", "middle"))

    # Function Tools -> External APIs
    elements.append(svg_arrow(ftx + ftw, fty + 70, eax, eay + 70, MUTED, dashed=True))
    elements.append(svg_text(920, fty + 60, "HTTP", MUTED, 9, "normal", "middle"))

    # Cloud Run Session Logger -> Firestore
    elements.append(svg_arrow(crx + crw/2 - 20, cry + crh, fsx + fsw/2, fsy, MUTED, dashed=True))
    elements.append(svg_text(crx + crw/2 + 30, cry + crh + 30, "Async Writes", MUTED, 9, "normal", "middle"))

    # Gemini -> Cloud Run (function calls return)
    elements.append(svg_arrow_path(
        f"M {glx + glw/2} {gly + glh} Q {glx + glw/2} {fty + 10} {ftx + ftw/2} {fty}",
        GOLD, dashed=True
    ))
    elements.append(svg_mono_text(glx + glw/2 + 10, gly + glh + 20, "Tool Calls", GOLD, 9))

    # ── Legend ──
    lx, ly = 950, 520
    elements.append(svg_rect(lx, ly, 190, 75, fill=SURFACE, stroke=BORDER, rx=5))
    elements.append(svg_text(lx + 10, ly + 18, "Legend", MUTED, 10, "bold"))
    # Solid gold line
    elements.append(f'<line x1="{lx+10}" y1="{ly+32}" x2="{lx+40}" y2="{ly+32}" stroke="{GOLD}" stroke-width="2"/>')
    elements.append(svg_mono_text(lx + 48, ly + 36, "Audio/Video Stream", MUTED, 9))
    # Solid blue line
    elements.append(f'<line x1="{lx+10}" y1="{ly+48}" x2="{lx+40}" y2="{ly+48}" stroke="{BLUE}" stroke-width="2"/>')
    elements.append(svg_mono_text(lx + 48, ly + 52, "SDK / Internal", MUTED, 9))
    # Dashed line
    elements.append(f'<line x1="{lx+10}" y1="{ly+64}" x2="{lx+40}" y2="{ly+64}" stroke="{MUTED}" stroke-width="2" stroke-dasharray="6,4"/>')
    elements.append(svg_mono_text(lx + 48, ly + 68, "External / Async", MUTED, 9))

    # ── Assemble SVG ──
    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}">
  <style>
    text {{ dominant-baseline: auto; }}
  </style>
  {chr(10).join(elements)}
</svg>'''
    return svg


def main():
    svg_content = generate_svg()

    # Ensure output directory exists
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    docs_dir = os.path.join(project_dir, "docs")
    os.makedirs(docs_dir, exist_ok=True)

    svg_path = os.path.join(docs_dir, "architecture-diagram.svg")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg_content)
    print(f"SVG saved: {svg_path}")

    # Try PNG export
    try:
        import cairosvg
        png_path = os.path.join(docs_dir, "architecture-diagram.png")
        cairosvg.svg2png(
            url=svg_path,
            write_to=png_path,
            output_width=2400,
            output_height=1400,
        )
        print(f"PNG exported: {png_path}")
    except ImportError:
        print("Install cairosvg for PNG export: pip install cairosvg")
        print("Or open the SVG in a browser and screenshot it.")


if __name__ == "__main__":
    main()
