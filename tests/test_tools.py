"""
ORÁCULO Tool Test Suite
========================
Run: python -m pytest tests/test_tools.py -v
Or:  python tests/test_tools.py (standalone)

Tests each tool for:
- Happy path (valid symbol returns formatted data)
- Error path (invalid symbol returns error dict, never raises)
- Cache behavior (second call returns cached data)
- Output format (all values are strings, voice-friendly)
"""
import asyncio
import pytest
import time
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import (
    get_stock_quote,
    get_market_news,
    get_technical_indicators,
    get_options_snapshot,
    _cache,
)


def _run(coro):
    """Helper to run async functions in sync test context."""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestStockQuote:
    def test_valid_symbol(self):
        result = _run(get_stock_quote("AAPL"))
        assert "error" not in result, f"Got error: {result.get('error')}"
        assert result["symbol"] == "AAPL"
        assert "$" in result["price"]  # Voice-formatted
        assert "source" in result

    def test_invalid_symbol_returns_error_dict(self):
        result = _run(get_stock_quote("ZZZZZZZZZ"))
        # Must return dict with error key, NEVER raise
        assert isinstance(result, dict)
        assert "error" in result or "price" in result  # Either error or fallback data

    def test_symbol_sanitization(self):
        result = _run(get_stock_quote("  aapl  "))
        assert result["symbol"] == "AAPL"  # Uppercased and stripped

    def test_cache_hit(self):
        _cache.clear()
        _run(get_stock_quote("SPY"))
        start = time.monotonic()
        result = _run(get_stock_quote("SPY"))
        elapsed = time.monotonic() - start
        assert elapsed < 0.01, f"Cache should be instant, took {elapsed:.3f}s"
        assert "error" not in result

    def test_output_format(self):
        result = _run(get_stock_quote("MSFT"))
        if "error" not in result:
            # All values should be strings (voice-friendly)
            for key, val in result.items():
                assert isinstance(val, str), f"{key} is {type(val)}, expected str"


class TestMarketNews:
    def test_ticker_news(self):
        result = _run(get_market_news("NVDA", limit=3))
        assert isinstance(result, dict)
        assert "count" in result
        # News may be empty if AV key not set, but should not error
        assert "error" not in result

    def test_topic_news(self):
        result = _run(get_market_news("Federal Reserve", limit=2))
        assert isinstance(result, dict)

    def test_limit_clamping(self):
        result = _run(get_market_news("AAPL", limit=100))
        # Should clamp to max 10
        if result.get("articles"):
            assert len(result["articles"]) <= 10


class TestTechnicalIndicators:
    def test_valid_symbol(self):
        result = _run(get_technical_indicators("AAPL"))
        assert "error" not in result, f"Got error: {result.get('error')}"
        assert "rsi_14" in result
        assert "rsi_interpretation" in result
        assert "macd" in result
        assert "sma_20" in result
        assert "source" in result

    def test_invalid_symbol(self):
        result = _run(get_technical_indicators("ZZZZZZZZZ"))
        assert isinstance(result, dict)
        # Should return error, not raise
        assert "error" in result

    def test_interpretation_labels_present(self):
        result = _run(get_technical_indicators("SPY"))
        if "error" not in result:
            assert result["rsi_interpretation"]  # Not empty
            assert result["macd_interpretation"]
            assert result["sma_trend"]


class TestOptionsSnapshot:
    def test_valid_optionable(self):
        result = _run(get_options_snapshot("SPY"))
        assert "error" not in result, f"Got error: {result.get('error')}"
        assert "put_call_ratio" in result
        assert "pcr_interpretation" in result
        assert "max_pain_price" in result
        assert "top_call_strikes_by_oi" in result

    def test_non_optionable_symbol(self):
        result = _run(get_options_snapshot("ZZZZZ"))
        assert isinstance(result, dict)
        # Should return error dict, not crash

    def test_max_pain_is_calculated(self):
        result = _run(get_options_snapshot("QQQ"))
        if "error" not in result:
            assert result["max_pain_price"] != "Unable to calculate"


class TestCrossToolIntegration:
    def test_all_tools_return_dicts(self):
        """Every tool must return a dict, no matter what."""
        for symbol in ["SPY", "AAPL", "ZZZZZ", "", "12345"]:
            for tool in [get_stock_quote, get_technical_indicators, get_options_snapshot]:
                result = _run(tool(symbol))
                assert isinstance(result, dict), (
                    f"{tool.__name__}({symbol!r}) returned {type(result)}"
                )

    def test_no_tool_raises(self):
        """No tool should ever raise an exception to the caller."""
        for symbol in ["SPY", "", "DROP TABLE", "../../etc/passwd", "A" * 1000]:
            for tool in [get_stock_quote, get_market_news, get_technical_indicators, get_options_snapshot]:
                try:
                    if tool == get_market_news:
                        _run(tool(symbol, limit=3))
                    else:
                        _run(tool(symbol))
                except Exception as e:
                    pytest.fail(
                        f"{tool.__name__}({symbol!r}) raised "
                        f"{type(e).__name__}: {e}"
                    )


# ── Standalone runner ──
if __name__ == "__main__":
    print("=" * 60)
    print("ORÁCULO Tool Test Suite")
    print("=" * 60)

    tests = [
        ("Stock Quote: AAPL", lambda: _run(get_stock_quote("AAPL"))),
        ("Stock Quote: invalid", lambda: _run(get_stock_quote("ZZZZZ"))),
        ("Market News: NVDA", lambda: _run(get_market_news("NVDA", 3))),
        ("Technicals: SPY", lambda: _run(get_technical_indicators("SPY"))),
        ("Options: QQQ", lambda: _run(get_options_snapshot("QQQ"))),
        ("Injection test", lambda: _run(get_stock_quote("'; DROP TABLE --"))),
        ("Empty string", lambda: _run(get_stock_quote(""))),
        ("Cache hit", lambda: _run(get_stock_quote("AAPL"))),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            start = time.monotonic()
            result = fn()
            elapsed = (time.monotonic() - start) * 1000
            status = "OK" if isinstance(result, dict) else "FAIL"
            has_error = "error" if "error" in result else "data"
            print(f"  [{status}] {name}: {has_error} ({elapsed:.0f}ms)")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: RAISED {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("All tests passed")
    else:
        print("FAILURES DETECTED")
        sys.exit(1)
