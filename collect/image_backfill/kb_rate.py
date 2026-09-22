"""Thread-safe request admission; a throttle immediately pauses all lanes."""
import json
import os
import threading
import time


class RequestGovernor:
    def __init__(self, directory, start, lo, hi):
        if not 0 < lo <= start <= hi:
            raise ValueError("require 0 < rps-min <= rps-start <= rps-max")
        self.lock = threading.Lock()
        self.rps, self.lo, self.hi = start, lo, hi
        self.next_at = self.blocked_until = 0.0
        self.streak = self.throttles = 0
        self.log = os.path.join(directory, "meta", "rate.log")
        self.state = os.path.join(directory, "meta", "rate_state.json")
        try:
            with open(self.state) as f:
                saved = json.load(f)
            self.rps = min(start, max(lo, saved["rps"]))
            self.blocked_until = time.monotonic() + max(0, saved["until"] - time.time())
            self.throttles = saved.get("throttles", 0)
        except (OSError, ValueError, KeyError):
            pass
        self._emit("start")

    def _emit(self, event, **extra):
        with open(self.log, "a") as f:
            f.write(json.dumps(dict(ts=time.time(), event=event, rps=self.rps, **extra)) + "\n")

    def _save(self):
        tmp = self.state + ".tmp"
        with open(tmp, "w") as f:
            json.dump(dict(rps=self.rps, until=time.time() + max(0, self.blocked_until - time.monotonic()),
                           throttles=self.throttles), f)
        os.replace(tmp, self.state)

    def pace(self, stop=None):
        # No reservation ahead of a sleep: a new 429 can invalidate every waiter.
        while True:
            if stop is not None and stop.is_set():
                return False
            with self.lock:
                now = time.monotonic()
                wait = max(self.next_at, self.blocked_until) - now
                if wait <= 0:
                    self.next_at = now + 1 / self.rps
                    return True
            if stop is not None:
                if stop.wait(min(wait, 1)):
                    return False
            else:
                time.sleep(min(wait, 1))

    def response(self, status, retry_after=0):
        with self.lock:
            now = time.monotonic()
            if status == 429:
                self.rps = max(self.lo, self.rps / 2)
                self.streak = 0
                self.throttles += 1
                wait = max(retry_after, min(3600, 60 * 2 ** min(self.throttles - 1, 6)))
                self.blocked_until = max(self.blocked_until, now + wait)
                self.next_at = max(self.next_at, self.blocked_until)
                self._emit("429", wait=wait)
                self._save()
            elif status == 503 or retry_after > 0:
                self.streak = 0
                self.blocked_until = max(self.blocked_until, now + max(retry_after, 15))
                self._emit("server_wait", status=status, wait=max(retry_after, 15))
                self._save()
            elif status == 200 and now >= self.blocked_until:
                self.streak += 1
                if self.streak >= 20:
                    # 恢复加速：+0.05/20成功（原 +0.01/100 太慢，被 429 减半后要 2.6h 才回上限）
                    self.rps = min(self.hi, self.rps + 0.05)
                    self.streak = 0
                    self.throttles = max(0, self.throttles - 1)
                    self._emit("ai")
                    self._save()
