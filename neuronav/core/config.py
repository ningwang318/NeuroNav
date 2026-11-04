"""Project paths and persisted-settings helpers.

Everything is resolved relative to the project root, never the working directory.
Shared settings live in config/ (versioned). Machine-specific paths live in
local.toml at the project root (gitignored; copy local.example.toml).
"""
from dataclasses import asdict
import json
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / 'config'
ASSETS_DIR = ROOT / 'assets'
LOCAL_SETTINGS = ROOT / 'local.toml'

DEFAULT_MOTIVE_INSTALL = r'C:\Program Files\OptiTrack\Motive'


# ------------------------------------------------------------------ JSON files
def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json_atomic(path, data):
    """Write JSON via a sibling .tmp file and an atomic replace. NaN/inf are rejected."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def load_settings(cls, path, key=None):
    """Load a settings dataclass (optionally from ``data[key]``) and validate it.

    A missing file yields the dataclass defaults.
    """
    path = Path(path)
    if path.exists():
        data = read_json(path)
        value = cls(**(data[key] if key else data))
    else:
        value = cls()
    value.validate()
    return value


def save_settings(value, path, key=None, **envelope):
    """Validate and atomically save a settings dataclass.

    Fields are the top level, or nested as {**envelope, key: fields} when ``key`` is given.
    """
    value.validate()
    fields = asdict(value)
    write_json_atomic(path, {**envelope, key: fields} if key else fields)


# ------------------------------------------------------ machine-specific paths
def local_settings():
    """Contents of local.toml, or {} when absent. See local.example.toml."""
    if not LOCAL_SETTINGS.exists():
        return {}
    with LOCAL_SETTINGS.open('rb') as handle:
        return tomllib.load(handle)


def local_path(section, name, default=None):
    """A path from local.toml, e.g. local_path('paths', 'mri_dir'); None/default when unset."""
    value = local_settings().get(section, {}).get(name)
    return Path(value) if value else default


def motive_install():
    return str(local_path('motive', 'install', DEFAULT_MOTIVE_INSTALL))


def resolve_config_path(value):
    """Relative paths in settings.ini are relative to config/; absolute paths are kept."""
    path = Path(value)
    return path if path.is_absolute() else CONFIG_DIR / path


def config_relative(value):
    """Store paths inside config/ relative to it, so the project folder can move."""
    path = Path(value)
    try:
        return str(path.resolve().relative_to(CONFIG_DIR.resolve()))
    except ValueError:
        return str(value)
