"""Pytest configuration for opencloud-docserver tests.

This file sets up the pytest environment and imports the src module
for use in tests, and registers a deterministic ``ci`` Hypothesis
profile (fewer examples, derandomized runs) that CI activates via the
``HYPOTHESIS_PROFILE=ci`` environment variable. Local/developer runs
keep the full-strength Hypothesis defaults.
"""
import os
import sys
from pathlib import Path

from hypothesis import settings

# Add the src directory to the Python path so tests can import from src.*
src_dir = Path(__file__).parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# CI profile: deterministic (derandomize -> fixed seed per test, ignores the
# local example database) and lighter (60 examples vs default 100). Only takes
# effect on tests that do NOT pin their own @settings.
settings.register_profile("ci", max_examples=60, derandomize=True)
profile = os.environ.get("HYPOTHESIS_PROFILE")
if profile:
    settings.load_profile(profile)
