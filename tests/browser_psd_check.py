"""PSD interaction coverage is consolidated in the current preview/download test."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).with_name('browser_smart_preview.py')),run_name='__main__')
