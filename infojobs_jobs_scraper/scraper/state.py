from __future__ import annotations

import json
from pathlib import Path


STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "state.json"


class RunState:
    def __init__(self) -> None:
        self._processed: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            ids = data.get("processed_ids", [])
            self._processed = set(ids)
        except Exception:
            self._processed = set()

    def save(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "processed_ids": sorted(self._processed),
        }
        STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    def is_processed(self, id_oferta: str) -> bool:
        return id_oferta in self._processed

    def mark(self, id_oferta: str) -> None:
        self._processed.add(id_oferta)

    @property
    def processed_count(self) -> int:
        return len(self._processed)
