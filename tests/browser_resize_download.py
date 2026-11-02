"""Current requirement: resize previews first, downloads only on explicit buttons."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).with_name('browser_smart_preview.py')),run_name='__main__')
