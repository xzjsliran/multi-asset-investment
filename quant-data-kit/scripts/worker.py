"""一个有时间上限的取数任务。凭证仅在此本地进程内使用。"""
import contextlib
import io
import json
import sys

from quantkit.common import now, token_value
from quantkit.providers import fetch


if __name__ == "__main__":
    spec = json.loads(sys.stdin.read())
    try:
        # 第三方库的进度条和提示不混入 JSON 返回值。
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = fetch(spec["op"], spec["params"])
        payload = {"ok": True, "fetched_at": now(), "result": result}
    except Exception as exc:
        import requests
        secret = token_value()
        error = str(exc)
        if secret:
            error = error.replace(secret, "[本地凭证]")
        retryable = isinstance(exc, (requests.Timeout, requests.ConnectionError))
        payload = {"ok": False, "error": type(exc).__name__ + ": " + error[:500], "retryable": retryable}
    print(json.dumps(payload, ensure_ascii=False, allow_nan=False))
