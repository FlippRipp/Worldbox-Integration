import sys
from pathlib import Path

# Tests import the module code directly, no WorldboxAI checkout needed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wb_toy_link"))
