import sys
from pathlib import Path

# Ensure project root is on sys.path so 'app' imports resolve
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
