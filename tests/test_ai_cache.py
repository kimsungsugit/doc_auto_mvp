from __future__ import annotations

import json

from app.services.ai_cache import AiResponseCache


class TestAiResponseCache:
    def test_miss_then_hit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AI_CACHE_ENABLED", "1")
        cache = AiResponseCache(tmp_path)

        assert cache.get("ns", "v1", "gpt-4o-mini", "hello") is None
        assert cache.misses == 1

        cache.set("ns", "prompt-v1", "gpt-4o-mini", "hello", {"result": 42})
        got = cache.get("ns", "prompt-v1", "gpt-4o-mini", "hello")
        assert got == {"result": 42}
        assert cache.hits == 1

    def test_different_prompt_version_invalidates(self, tmp_path):
        cache = AiResponseCache(tmp_path)
        cache.set("ns", "v1", "gpt-4o", "text", {"a": 1})
        assert cache.get("ns", "v2", "gpt-4o", "text") is None
        assert cache.get("ns", "v1", "gpt-4o", "text") == {"a": 1}

    def test_different_model_invalidates(self, tmp_path):
        cache = AiResponseCache(tmp_path)
        cache.set("ns", "v1", "gpt-4o-mini", "same text", {"m": "mini"})
        assert cache.get("ns", "v1", "gpt-4o", "same text") is None

    def test_disabled_cache_always_misses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AI_CACHE_ENABLED", "0")
        cache = AiResponseCache(tmp_path)
        cache.set("ns", "v1", "gpt-4o-mini", "x", {"skip": True})
        assert cache.get("ns", "v1", "gpt-4o-mini", "x") is None

    def test_corrupt_cache_file_recovers(self, tmp_path):
        cache = AiResponseCache(tmp_path)
        key = cache._make_key("ns", "v1", "gpt-4o", "t")
        (tmp_path / f"{key}.json").write_text("{invalid", encoding="utf-8")
        assert cache.get("ns", "v1", "gpt-4o", "t") is None

    def test_stats_tracks_hits_and_ratio(self, tmp_path):
        cache = AiResponseCache(tmp_path)
        cache.set("ns", "v1", "gpt-4o", "a", {"x": 1})
        cache.get("ns", "v1", "gpt-4o", "a")  # hit
        cache.get("ns", "v1", "gpt-4o", "b")  # miss
        cache.get("ns", "v1", "gpt-4o", "a")  # hit

        stats = cache.stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_ratio_percent"] == 67
        assert stats["entry_count"] >= 1

    def test_size_cap_evicts_oldest(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AI_CACHE_MAX_ENTRIES", "3")
        cache = AiResponseCache(tmp_path)
        for i in range(6):
            cache.set("ns", "v1", "gpt-4o", f"text-{i}", {"i": i})
        remaining = list(tmp_path.glob("*.json"))
        assert len(remaining) <= 3, f"Expected <=3 entries after eviction, got {len(remaining)}"

    def test_full_text_prevents_prefix_collision(self, tmp_path):
        """Two documents sharing a long prefix must not collide."""
        cache = AiResponseCache(tmp_path)
        prefix = "A" * 6000
        cache.set("ns", "v1", "gpt-4o", prefix + "suffix-doc-1", {"doc": 1})
        result = cache.get("ns", "v1", "gpt-4o", prefix + "suffix-doc-2")
        assert result is None

    def test_prompt_change_invalidates_automatically(self, tmp_path):
        """Passing a different prompt string must yield a different cache key."""
        cache = AiResponseCache(tmp_path)
        cache.set("ns", "Please extract fields", "gpt-4o", "text", {"x": 1})
        # Same namespace/model/text, different prompt → miss
        assert cache.get("ns", "Please extract fields VERY CAREFULLY", "gpt-4o", "text") is None

    def test_concurrent_set_does_not_corrupt(self, tmp_path, monkeypatch):
        """Parallel writes must not produce partial files or evict a just-written entry."""
        from concurrent.futures import ThreadPoolExecutor

        monkeypatch.setenv("AI_CACHE_MAX_ENTRIES", "10")
        cache = AiResponseCache(tmp_path)

        def _write(i: int) -> None:
            cache.set("ns", "p", "gpt-4o", f"text-{i}", {"i": i, "payload": "x" * 100})

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_write, range(40)))

        # Each remaining file should be parseable (no partial writes)
        for p in tmp_path.glob("*.json"):
            json.loads(p.read_text(encoding="utf-8"))  # raises if corrupt
        # Size cap respected (allow 1 over due to race window acceptability)
        assert len(list(tmp_path.glob("*.json"))) <= 11


class TestStructurerUsesCache:
    """OpenAIStructurer.classify_and_extract must hit cache on repeated calls."""

    def test_second_call_hits_cache_not_api(self, tmp_path, monkeypatch):
        from app.services import ai_cache as ai_cache_module
        from app.services.ai_structurer import OpenAIStructurer

        # Use a fresh cache pointed at tmp_path
        fresh_cache = ai_cache_module.AiResponseCache(tmp_path)
        monkeypatch.setattr(ai_cache_module, "_default_cache", fresh_cache)

        monkeypatch.setenv("OPENAI_STRUCTURING_ENABLED", "1")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        call_count = 0

        class FakeResponse:
            output_text = json.dumps({
                "document_type": "영수증",
                "classification_confidence": 0.95,
                "fields": [
                    {"field_name": "document_type", "value": "영수증", "confidence": 0.95},
                    {"field_name": "approval_no", "value": "12345678", "confidence": 0.9},
                ],
            })

        class FakeClient:
            class responses:
                @staticmethod
                def create(**_kwargs):
                    nonlocal call_count
                    call_count += 1
                    return FakeResponse()

        structurer = OpenAIStructurer()
        monkeypatch.setattr(structurer, "_get_client", lambda: FakeClient())

        text = "현금영수증 샘플 텍스트 승인번호 12345678"
        _, _, fields1 = structurer.classify_and_extract(text)
        _, _, fields2 = structurer.classify_and_extract(text)

        assert call_count == 1, f"Expected 1 API call (cache hit second time), got {call_count}"
        assert len(fields1) == len(fields2)
        assert fresh_cache.hits == 1
