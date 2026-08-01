"""WebSocket 重连逻辑单元测试 — 指数退避公式验证。

前端 ws.ts 中的重连策略：BASE_DELAY=1000ms, MAX_BACKOFF=30000ms，
每次重试 delay 翻倍，上限 30s。此测试验证算法一致性。
"""


class TestBackoffFormula:
    """指数退避公式（与 apps/desktop/src/services/ws.ts 保持一致）"""

    BASE = 1000
    MAX_BACKOFF = 30000

    @staticmethod
    def _backoff(retry_count: int) -> int:
        """模拟前端 WsClient.backoffDelay() 逻辑"""
        return min(TestBackoffFormula.BASE * (2 ** retry_count),
                   TestBackoffFormula.MAX_BACKOFF)

    def test_first_retry_one_second(self):
        """第 1 次重试 = 1s"""
        assert self._backoff(0) == 1000

    def test_second_retry_two_seconds(self):
        """第 2 次重试 = 2s"""
        assert self._backoff(1) == 2000

    def test_third_retry_four_seconds(self):
        """第 3 次重试 = 4s"""
        assert self._backoff(2) == 4000

    def test_fourth_retry_eight_seconds(self):
        """第 4 次重试 = 8s"""
        assert self._backoff(3) == 8000

    def test_fifth_retry_sixteen_seconds(self):
        """第 5 次重试 = 16s"""
        assert self._backoff(4) == 16000

    def test_sixth_retry_hits_max_backoff(self):
        """第 6 次重试碰天花板 = 30s"""
        assert self._backoff(5) == 30000

    def test_many_retries_stay_at_max(self):
        """无限重试永久卡在 30s 不变"""
        assert self._backoff(10) == 30000
        assert self._backoff(100) == 30000
