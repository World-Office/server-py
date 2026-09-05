"""Pytest configuration for opencloud-docserver tests.

This file sets up the pytest environment and imports the src module
for use in tests.
"""
import sys
from pathlib import Path

# Add the src directory to the Python path so tests can import from src.*
src_dir = Path(__file__).parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# Import pytest fixture registration here if needed
