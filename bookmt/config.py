"""Config loading. Fails loudly on a missing key rather than defaulting silently."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

_MISSING = object()


class ConfigError(RuntimeError):
    pass


class Config:
    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, dotted: str, default: Any = _MISSING) -> Any:
        """Fetch a nested key by dotted path, e.g. cfg.get("qa.vision_dpi")."""
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is _MISSING:
                    raise ConfigError(f"config.yaml is missing required key: {dotted}")
                return default
            node = node[part]
        return node

    def path(self, dotted: str) -> Path:
        """Resolve a path-valued config key relative to the project root."""
        raw = self.get(dotted)
        p = Path(raw)
        return p if p.is_absolute() else (ROOT / p).resolve()


def load_config(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"config.yaml not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        return Config(yaml.safe_load(f))


def ensure_dirs(cfg: Config) -> None:
    for key in ("paths.workdir", "paths.output_dir", "paths.images_dir", "paths.font_dir"):
        cfg.path(key).mkdir(parents=True, exist_ok=True)


def api_key() -> str:
    """Read OPENAI_API_KEY from the environment or .env. Never logs the value."""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise ConfigError(
            "OPENAI_API_KEY is not set. Add it to .env in the project root:\n"
            "    OPENAI_API_KEY=sk-..."
        )
    return key
