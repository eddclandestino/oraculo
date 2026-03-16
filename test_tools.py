"""
ORÁCULO Tools Test Script
Run with: python test_tools.py

Tests all four tools for correctness, error handling, and cache behavior.
"""
import asyncio
import json
import time
import sys


async def main():
    # Import after ensuring we're in the right directory
    from tools import (
        get_stock_quote,
        get_market_news,
        get_technical_indicators,
        get_options_snapshot,
    )

    passed = 0
    failed = 0

    def _print_result(name, result):
        print(f"\n{'='*60}")
        print(f"TEST: {name}")
        print(f"{'='*60}")
        print(json.dumps(result, indent=2, default=str))

    def _check(name, result, condition, msg=""):
        nonlocal passed, failed
        if condition:
            print(f"  PASS: {msg or name}")
            passed += 1
        else:
            print(f"  FAIL: {msg or name}")
            failed += 1

    # ── Test 1: Stock quote — valid ticker ──
    result = await get_stock_quote("SPY")
    _print_result("Stock Quote: SPY", result)
    _check("quote_spy", result, "error" not in result, "SPY returns valid data")
    _check("quote_spy_price", result, result.get("price", "").startswith("$"), "Price is formatted with $")
    _check("quote_spy_volume", result, "volume" in result, "Volume field present")

    # ── Test 2: Stock quote — invalid ticker ──
    result = await get_stock_quote("ZZZZZ")
    _print_result("Stock Quote: ZZZZZ (invalid)", result)
    _check("quote_invalid", result, "error" in result or "price" in result,
           "Invalid symbol returns error dict or fallback data (no crash)")

    # ── Test 3: Market news — by ticker ──
    result = await get_market_news("NVDA", limit=3)
    _print_result("Market News: NVDA", result)
    _check("news_nvda", result, isinstance(result.get("count", -1), int), "Returns count field")
    _check("news_nvda_no_crash", result, True, "Did not crash")

    # ── Test 4: Market news — by topic ──
    result = await get_market_news("Federal Reserve", limit=3)
    _print_result("Market News: Federal Reserve", result)
    _check("news_topic", result, "query" in result, "Returns query field")

    # ── Test 5: Technical indicators ──
    result = await get_technical_indicators("AAPL")
    _print_result("Technical Indicators: AAPL", result)
    _check("tech_aapl", result, "error" not in result, "AAPL returns valid indicators")
    _check("tech_rsi", result, "rsi_14" in result, "RSI field present")
    _check("tech_rsi_interp", result, "rsi_interpretation" in result, "RSI interpretation present")
    _check("tech_macd", result, "macd" in result, "MACD field present")
    _check("tech_sma", result, "sma_20" in result, "SMA 20 field present")
    _check("tech_bb", result, "bollinger_upper" in result, "Bollinger upper field present")

    # ── Test 6: Options snapshot ──
    result = await get_options_snapshot("SPY")
    _print_result("Options Snapshot: SPY", result)
    _check("options_spy", result, "error" not in result, "SPY returns valid options data")
    _check("options_pcr", result, "put_call_ratio" in result, "P/C ratio present")
    _check("options_max_pain", result, "max_pain_price" in result, "Max pain present")
    _check("options_strikes", result, "top_call_strikes_by_oi" in result, "Top call strikes present")

    # ── Test 7: Options — potentially limited ticker ──
    result = await get_options_snapshot("BRK-A")
    _print_result("Options Snapshot: BRK-A", result)
    _check("options_brk", result, True, "BRK-A did not crash")

    # ── Test 8: Cache hit ──
    start = time.time()
    result = await get_stock_quote("SPY")
    elapsed = time.time() - start
    _print_result(f"Cache Test: SPY ({elapsed*1000:.0f}ms)", result)
    _check("cache_fast", result, elapsed < 0.1, f"Cached result in {elapsed*1000:.0f}ms (should be <100ms)")

    # ── Summary ──
    print(f"\n{'='*60}")
    print(f"RESULTS: {passed} passed, {failed} failed, {passed+failed} total")
    print(f"{'='*60}")
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
