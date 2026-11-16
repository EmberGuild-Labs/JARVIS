"""Launcher for macOS (and anywhere else): python run.py [--startup]"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if "--startup" in sys.argv:
    time.sleep(20)  # let the login finish before loading models

from jarvis.__main__ import main

main()
