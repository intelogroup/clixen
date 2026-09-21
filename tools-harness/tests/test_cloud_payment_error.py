from clients.cloud_client import _is_payment_error


class _E(Exception):
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.status_code = status_code


def test_quota_errors_are_payment_errors():
    assert _is_payment_error(_E("x", 402))
    assert _is_payment_error(_E("x", 429))
    assert _is_payment_error(_E("Rate limit exceeded: free-models-per-day-high-balance"))
    assert _is_payment_error(_E("credit_balance_exhausted"))


def test_incidental_429_digits_are_not():
    assert not _is_payment_error(_E("request id req_4291 failed", 500))
    assert not _is_payment_error(_E("connection reset"))
