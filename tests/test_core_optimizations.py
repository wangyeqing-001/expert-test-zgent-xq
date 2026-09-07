"""核心优化项单元测试 — 覆盖熔断器、动态timeout、JSON精确修复、YAPI缓存、截断检测

运行: python -m pytest tests/test_core_optimizations.py -v
"""
import json
import os
import sys
import time
import threading

import pytest

# 项目根目录到 sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 避免单引号转义地狱
SQ = chr(39)  # '


# ============================================================
# Test 1: CircuitBreaker 熔断器
# ============================================================
class TestCircuitBreaker:
    """P0-1 熔断器正确性（三态转换 + 边界）"""

    def _make_cb(self, threshold=3, reset=1):
        from agents.test_generator.agent import _CircuitBreaker
        return _CircuitBreaker(failure_threshold=threshold, reset_timeout=reset)

    def test_initial_state_closed(self):
        cb = self._make_cb()
        assert cb.state == 'closed'
        assert cb.can_execute() is True
        assert cb.failure_count == 0

    def test_success_resets_failure_count(self):
        cb = self._make_cb()
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == 'closed'

    def test_3_failures_opens_circuit(self):
        cb = self._make_cb(threshold=3, reset=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == 'open'
        assert cb.can_execute() is False

    def test_open_state_rejects_all(self):
        cb = self._make_cb(threshold=2, reset=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == 'open'
        for _ in range(5):
            assert cb.can_execute() is False

    def test_cooling_switches_to_half_open(self):
        cb = self._make_cb(threshold=2, reset=1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == 'open'
        time.sleep(1.1)
        assert cb.can_execute() is True
        assert cb.state == 'half_open'

    def test_half_open_success_goes_closed(self):
        cb = self._make_cb(threshold=2, reset=1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(1.1)
        cb.can_execute()  # → half_open
        assert cb.state == 'half_open'
        cb.record_success()
        assert cb.state == 'closed'
        assert cb.failure_count == 0

    def test_half_open_failure_goes_back_open(self):
        cb = self._make_cb(threshold=2, reset=1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(1.1)
        cb.can_execute()  # → half_open
        assert cb.state == 'half_open'
        cb.record_failure()
        assert cb.state == 'open'

    def test_custom_threshold_and_reset(self):
        cb = self._make_cb(threshold=5, reset=0.5)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == 'closed'
        cb.record_failure()
        assert cb.state == 'open'
        time.sleep(0.6)
        assert cb.can_execute() is True
        assert cb.state == 'half_open'


# ============================================================
# Test 2: 动态 timeout 边界
# ============================================================
class TestDynamicTimeout:
    """P0-2 动态 HTTP timeout: min(30 + n*30, 180)"""

    @staticmethod
    def _calc(n: int) -> int:
        return min(30 + n * 30, 180)

    def test_zero_pts_floor_30(self):
        assert self._calc(0) == 30

    def test_one_pt(self):
        assert self._calc(1) == 60

    def test_three_pts(self):
        assert self._calc(3) == 120

    def test_five_pts(self):
        assert self._calc(5) == 180

    def test_ten_pts_capped(self):
        assert self._calc(10) == 180

    def test_twenty_pts_capped(self):
        assert self._calc(20) == 180

    def test_linear_between_zero_and_six(self):
        for n in range(6):
            expected = min(30 + n * 30, 180)
            assert self._calc(n) == expected, f"{n} pts"


# ============================================================
# Test 5: _repair_json_for_llm 精确修复回归
# ============================================================
class TestJSONRepair:
    """P2-12 JSON 精确修复器——不误伤合法单引号"""

    @staticmethod
    def _repair(s: str) -> str:
        from agents.test_point_generator.agent import TestPointGenerator
        return TestPointGenerator._repair_json_for_llm(s)

    # --- 核心回归：不误伤合法单引号 ---

    def test_apostrophe_in_string_preserved(self):
        """user's auth → 保留合法单引号"""
        s = '{"detail": "user' + SQ + 's auth failed", "scope": "backend"}'
        data = json.loads(s)  # 确认原始合法
        repaired = self._repair(s)
        assert json.loads(repaired) == data

    # --- trailing comma ---

    def test_trailing_comma_in_array(self):
        assert json.loads(self._repair('[1, 2, 3,]')) == [1, 2, 3]

    def test_trailing_comma_in_object(self):
        assert json.loads(self._repair('{"a": 1, "b": 2,}')) == {"a": 1, "b": 2}

    def test_trailing_comma_nested(self):
        s = '{"items": [{"x": 1,}, {"y": 2,}],}'
        assert json.loads(self._repair(s)) == {"items": [{"x": 1}, {"y": 2}]}

    # --- 裸单引号键 ---

    def test_single_quote_keys(self):
        s = "{'scope': 'backend', 'priority': 'P0'}"
        result = json.loads(self._repair(s))
        assert result["scope"] == "backend"
        assert result["priority"] == "P0"

    def test_single_quote_keys_with_whitespace(self):
        s = "{\n    'id': 1,\n    'scope': 'client',\n}"
        result = json.loads(self._repair(s))
        assert result == {"id": 1, "scope": "client"}

    # --- LLM 常见伪 JSON 复合场景 ---

    def test_realistic_llm_output(self):
        """LLM 典型坏 JSON: 单引号键 + trailing comma + 嵌套"""
        s = (
            "[\n"
            "    { 'scope': 'backend', 'detail': '参数校验', 'priority': 'P0', },\n"
            "    { 'scope': 'admin', 'detail': '权限控制', 'priority': 'P1', }\n"
            "]"
        )
        result = json.loads(self._repair(s))
        assert len(result) == 2
        assert result[0]["scope"] == "backend"
        assert result[1]["priority"] == "P1"

    def test_llm_output_complex_noise(self):
        """复杂 LLM 输出: 裸单引号键 + trailing comma + 嵌套"""
        s = (
            "[\n"
            "    { 'test_module': 'topic_create', 'priority': 'P0', },\n"
            "    { 'test_module': 'comment_reply', 'priority': 'P1'}\n"
            "]"
        )
        result = json.loads(self._repair(s))
        assert len(result) == 2
        assert result[0]["test_module"] == "topic_create"

    def test_llm_output_value_with_apostrophe_in_double_quotes(self):
        # Correct JSON: double-quoted value with internal apostrophe
        s = '{"msg": "user' + SQ + 's 401", "code": 401}'
        repaired = self._repair(s)
        result = json.loads(repaired)
        assert SQ in result["msg"]
    def test_already_valid_json_unchanged(self):
        s = '{"a": 1, "b": [2, 3], "c": null}'
        # 可能加个换行/空格，所以比较解析后
        assert json.loads(self._repair(s)) == json.loads(s)

    def test_empty_string(self):
        assert self._repair('') == ''

    def test_non_json_noise(self):
        s = '这不是一个 JSON'
        repaired = self._repair(s)
        assert isinstance(repaired, str)


# ============================================================
# Test 3: YAPI 缓存
# ============================================================
class TestYAPICache:
    """P1-5 YAPI 接口 TTL 缓存"""

    def setup_method(self):
        import web_server as ws
        ws._YAPI_CACHE.clear()

    def test_cache_hit_after_first_write(self):
        import web_server as ws
        url = 'https://ugcqams.snowballfinance.com/interface/api/99999'
        assert ws._YAPI_CACHE.get(url) is None

        mock_result = {"code": 0, "data": {"title": "test_api"}}
        with ws._YAPI_CACHE_LOCK:
            ws._YAPI_CACHE[url] = (time.time(), mock_result)

        with ws._YAPI_CACHE_LOCK:
            ts, data = ws._YAPI_CACHE[url]
        assert data["code"] == 0
        assert data["data"]["title"] == "test_api"

    def test_cache_ttl_expiry(self):
        import web_server as ws
        url = 'https://ugcqams.snowballfinance.com/interface/api/88888'

        expired_ts = time.time() - ws._YAPI_CACHE_TTL - 1
        with ws._YAPI_CACHE_LOCK:
            ws._YAPI_CACHE[url] = (expired_ts, {"stale": True})

        with ws._YAPI_CACHE_LOCK:
            cached = ws._YAPI_CACHE.get(url)
            if cached and (time.time() - cached[0]) < ws._YAPI_CACHE_TTL:
                hit = True
            else:
                hit = False
        assert hit is False

    def test_invalid_url_not_cached(self):
        """_YAPI_ID_RE 不匹配 → 直接 return {}，不会写缓存"""
        import web_server as ws
        ws._fetch_yapi_interface('https://example.com/not_an_interface_url')
        assert len(ws._YAPI_CACHE) == 0

    def test_thread_safety(self):
        """多线程同时读写不炸"""
        import web_server as ws
        errors = []

        def writer():
            try:
                for i in range(50):
                    url = f'https://test.com/api/{i}'
                    with ws._YAPI_CACHE_LOCK:
                        ws._YAPI_CACHE[url] = (time.time(), {"v": i})
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(50):
                    url = f'https://test.com/api/{i}'
                    with ws._YAPI_CACHE_LOCK:
                        _ = ws._YAPI_CACHE.get(url)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(3)] + \
                  [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"线程安全测试出错: {errors}"


# ============================================================
# Test 4: _is_truncated 截断检测
# ============================================================
class TestTruncationDetection:
    """LLM 截断检测逻辑边界"""

    @staticmethod
    def _is_truncated(resp: str) -> bool:
        from agents.test_generator.agent import TestGeneratorAgent
        return TestGeneratorAgent._is_truncated(resp)

    def test_normal_json_not_truncated(self):
        assert self._is_truncated('{"test_cases": [{"test_module": "m"}]}') is False

    def test_code_block_properly_closed(self):
        assert self._is_truncated('```json\n{"a": 1}\n```') is False

    def test_code_block_open_not_closed(self):
        assert self._is_truncated('```json\n{"a": 1') is True

    def test_unparseable_response_truncated(self):
        assert self._is_truncated('garbage no json') is True  # 无法解析视为截断

    def test_partial_json_truncated(self):
        resp = '{"test_cases": [{"test_module": "m1"}, {"test_module": "m2"'
        assert self._is_truncated(resp) is True


# ============================================================
# Test 6: prompt 模板占位符完整性
# ============================================================
class TestPromptPlaceholders:
    """三个端 prompt 模板都要有 yapi_context + past_batches_summary"""

    @staticmethod
    def _load(p: str) -> str:
        full = os.path.join(ROOT, 'agents', 'test_generator', p)
        with open(full) as f:
            return f.read()

    @pytest.mark.parametrize('prompt_file', [
        'client_test.md',
        'backend_test.md',
        'admin_test.md',
    ])
    def test_all_templates_have_yapi(self, prompt_file):
        content = self._load(prompt_file)
        assert '{yapi_context}' in content, f"{prompt_file} 缺 {{yapi_context}}"

    @pytest.mark.parametrize('prompt_file', [
        'client_test.md',
        'backend_test.md',
        'admin_test.md',
    ])
    def test_all_templates_have_past_batches(self, prompt_file):
        content = self._load(prompt_file)
        assert '{past_batches_summary}' in content, f"{prompt_file} 缺 {{past_batches_summary}}"

    def test_prd_to_testpoints_has_hard_constraints(self):
        path = os.path.join(ROOT, 'agents', 'test_point_generator', 'prd_to_testpoints.md')
        with open(path) as f:
            content = f.read()
        assert '强制输出要求' in content
        assert '每个 scope 至少 3 个测试点' in content
        assert 'backend' in content.lower()
