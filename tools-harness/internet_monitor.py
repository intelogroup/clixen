"""Minimal internet connectivity monitor. Logs UP/DOWN transitions. Zero deps beyond asyncio."""

import asyncio
import logging

log = logging.getLogger(__name__)

_ENDPOINTS = [
    ("1.1.1.1", 443),
    ("api.telegram.org", 443),
    ("google.com", 443),
]
_CHECK_INTERVAL = 30

_WARN = "\n".join(
    [
        "Internet appears to be DOWN. Bot will be unable to receive/send messages.",
        "Check your connection, VPN, or proxy settings.",
        f"Endpoints tested: {', '.join(h for h, _ in _ENDPOINTS)}",
    ]
)
_OK = "Internet connectivity restored."


class InternetMonitor:
    def __init__(self, interval: int = _CHECK_INTERVAL):
        self._state: bool | None = None
        self._interval = interval

    async def check(self) -> bool:
        for host, port in _ENDPOINTS:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=5
                )
                writer.close()
                await writer.wait_closed()
                return True
            except Exception:
                continue
        return False

    async def run(self):
        log.info("Internet monitor started (interval=%ds)", self._interval)
        while True:
            ok = await self.check()
            if self._state is None:
                if ok:
                    log.info("Internet: UP")
                else:
                    log.warning(_WARN)
            elif self._state and not ok:
                log.warning(_WARN)
            elif not self._state and ok:
                log.info(_OK)
            self._state = ok
            await asyncio.sleep(self._interval)
