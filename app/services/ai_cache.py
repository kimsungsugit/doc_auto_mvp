"""File-based cache for OpenAI response payloads.

Keyed by sha256(namespace + prompt_fingerprint + model + full_text). Stored as JSON under
`storage/ai_cache/`. Avoids duplicate API spend when the same OCR/PDF text is re-extracted.

Design notes:
- **Full-text hash**: uses the entire input text, not a prefix → no key collision on docs
  sharing a long boilerplate header.
- **Prompt fingerprint**: caller passes the actual prompt string; its hash is mixed into the
  key, so any prompt edit silently invalidates stale entries without bumping a version.
- **Atomic writes**: write to `*.tmp` then rename → no partial files on concurrent writes.
- **Size cap**: when entry count exceeds `AI_CACHE_MAX_ENTRIES` (default 2000), oldest
  files by mtime are pruned.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AiResponseCache:
    """Simple content-addressable cache for AI response payloads."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path(os.getenv("AI_CACHE_DIR", "storage/ai_cache"))
        self.enabled = os.getenv("AI_CACHE_ENABLED", "1").lower() in {"1", "true", "yes"}
        self.max_entries = int(os.getenv("AI_CACHE_MAX_ENTRIES", "2000"))
        self.hits = 0
        self.misses = 0
        self._write_lock = threading.Lock()

    @staticmethod
    def _fingerprint(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def _make_key(self, namespace: str, prompt: str, model: str, text: str) -> str:
        h = hashlib.sha256()
        for part in (namespace, self._fingerprint(prompt), model, text):
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()

    def _path(self, key: str) -> Path:
        return self.base_dir / f"{key}.json"

    def get(self, namespace: str, prompt: str, model: str, text: str) -> Any | None:
        if not self.enabled:
            return None
        key = self._make_key(namespace, prompt, model, text)
        path = self._path(key)
        if not path.exists():
            self.misses += 1
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.hits += 1
            logger.debug("AI cache hit: %s", key[:12])
            return payload
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("AI cache read failed for %s: %s", key[:12], exc)
            self.misses += 1
            return None

    def set(self, namespace: str, prompt: str, model: str, text: str, payload: Any) -> None:
        if not self.enabled:
            return
        key = self._make_key(namespace, prompt, model, text)
        path = self._path(key)
        # Hold the lock across write + prune so concurrent writers don't race against eviction.
        with self._write_lock:
            try:
                self.base_dir.mkdir(parents=True, exist_ok=True)
                tmp_path = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.tmp")
                tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp_path, path)
                logger.debug("AI cache set: %s", key[:12])
            except OSError as exc:
                logger.warning("AI cache write failed for %s: %s", key[:12], exc)
                return
            self._prune_locked(protected=path)

    def _prune_locked(self, protected: Path | None = None) -> None:
        """Caller must hold `_write_lock`. Evicts oldest entries, never the just-written one."""
        if self.max_entries <= 0:
            return
        try:
            entries = list(self.base_dir.glob("*.json"))
        except OSError:
            return
        if len(entries) <= self.max_entries:
            return
        entries.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0)
        to_remove = len(entries) - self.max_entries
        removed = 0
        for old in entries:
            if removed >= to_remove:
                break
            if protected is not None and old.resolve() == protected.resolve():
                continue
            try:
                old.unlink()
                removed += 1
            except OSError as exc:
                logger.debug("AI cache prune skip %s: %s", old.name, exc)

    def stats(self) -> dict[str, int]:
        total = self.hits + self.misses
        ratio = (self.hits / total) if total else 0.0
        entry_count = 0
        if self.base_dir.exists():
            try:
                entry_count = sum(1 for _ in self.base_dir.glob("*.json"))
            except OSError:
                pass
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_ratio_percent": round(ratio * 100),
            "entry_count": entry_count,
            "max_entries": self.max_entries,
        }


_default_cache: AiResponseCache | None = None


def get_default_cache() -> AiResponseCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = AiResponseCache()
    return _default_cache
