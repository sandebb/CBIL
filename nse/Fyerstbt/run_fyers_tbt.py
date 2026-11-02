"""Kept so `python run_fyers_tbt.py` still works - everything lives in fyers_tbt_streamer.py."""
import runpy
import sys
from pathlib import Path

target = Path(__file__).with_name("fyers_tbt_streamer.py")
sys.argv[0] = str(target)
runpy.run_path(str(target), run_name="__main__")
