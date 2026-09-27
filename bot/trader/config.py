import json
import os
from pathlib import Path


def load_env(path=".env"):
    """Minimal .env loader so credentials never live in the config or in git."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path):
    with open(path) as f:
        return json.load(f)
