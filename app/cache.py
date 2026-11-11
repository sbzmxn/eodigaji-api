"""디스크 JSON 캐시 (TTL). 키는 content_id·시군구 코드 같은 '장소/참조 데이터' 식별자만 쓴다.
사용자 좌표는 절대 키나 값으로 넣지 않는다."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


class DiskCache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._mem: dict[tuple[str, str], dict] = {}
        self._keys: dict[str, set[str]] = {}

    def _path(self, ns: str, key: str) -> Path:
        return self.root / _SAFE.sub("_", ns) / f"{_SAFE.sub('_', key)}.json"

    def keys(self, ns: str) -> set[str]:
        """네임스페이스에 저장된 키 목록 (디렉터리는 처음 한 번만 훑는다)."""
        if ns not in self._keys:
            d = self.root / _SAFE.sub("_", ns)
            self._keys[ns] = {p.stem for p in d.glob("*.json")} if d.exists() else set()
        return self._keys[ns]

    def get_entry(self, ns: str, key: str) -> Optional[dict]:
        """{saved_at, value, meta} 또는 None (TTL 판단은 호출자)."""
        mk = (ns, key)
        if mk in self._mem:
            return self._mem[mk]
        p = self._path(ns, key)
        if not p.exists():
            return None
        try:
            entry = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        self._mem[mk] = entry
        return entry

    def get(self, ns: str, key: str, ttl_s: Optional[float]) -> Optional[Any]:
        entry = self.get_entry(ns, key)
        if entry is None:
            return None
        if ttl_s is not None and time.time() - entry.get("saved_at", 0) > ttl_s:
            return None
        return entry.get("value")

    def set(self, ns: str, key: str, value: Any, meta: Optional[dict] = None) -> None:
        entry = {"saved_at": time.time(), "value": value, "meta": meta or {}}
        p = self._path(ns, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False)
            os.replace(tmp, p)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        self._mem[(ns, key)] = entry
        self.keys(ns).add(p.stem)
