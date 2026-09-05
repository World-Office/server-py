"""Converter benchmark and performance-regression gate.

This package holds:

- ``benchmark_converter.py`` — a standalone harness that measures the
  DOCX <-> HTML roundtrip time on a synthetic N-paragraph fixture and
  asserts an upper bound (run it with ``uv run python
  tests/bench/benchmark_converter.py``).
- ``test_converter_perf.py`` — pytest performance-regression tests that
  re-use the same fixtures and bounds so the normal test suite also
  guards converter throughput.
"""
