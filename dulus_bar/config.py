"""User settings, persisted as JSON next to hub.json. Env vars still win."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from . import protocol as P

DEFAULTS: Dict[str, Any] = {
    "autohide": True,            # rest as a slim tab at the top edge; reveal on hover
    "notifications": True,       # system notifications for approvals / finished agents
    "reveal_on_activity": True,  # peek open briefly when an agent finishes or fails
    "do_not_disturb": False,     # no notifications, no peeks (approvals still show)
}


class Settings:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or P.app_dir() / "config.json"
        self._data: Dict[str, Any] = dict(DEFAULTS)
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        if isinstance(stored, dict):
            self._data.update({k: v for k, v in stored.items() if k in DEFAULTS})

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(self._data, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            print(f"[dulusbar] could not save settings to {self.path}: {exc}")

    def toggle(self, key: str) -> bool:
        self.set(key, not self[key])
        return bool(self[key])
