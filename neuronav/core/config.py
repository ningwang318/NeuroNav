"""Project paths. Everything is resolved relative to the project root, never the CWD."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / 'config'
ASSETS_DIR = ROOT / 'assets'
