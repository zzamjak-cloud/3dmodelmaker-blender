"""HTTP 요청을 자식 파이썬 프로세스에서 수행하는 워커 — 표준 라이브러리만 쓰고 패키지를 임포트하지 않는다.

Blender 확장은 스레드를 쓸 수 없고(검증기 py.no_threading, 실제 크래시 원인) urllib 은 논블로킹 폴링이 안 되므로,
요청 하나를 이 스크립트를 실행하는 자식 프로세스에 맡기고 애드온은 CLI 잡과 같은 타이머 펌프로 종료를 폴링한다.

사용: python http_worker.py <spec.json>
spec: {"url", "method", "headers": {}, "body_file": 요청 본문 파일|null, "timeout": 초,
       "out_file": 응답 본문을 쓸 파일, "result_file": 결과 JSON 파일,
       "poll": null | {"url_prefix": 결과 URL 앞부분, "job_id_field": "job_id", "pending_status": 202,
                       "interval": 초, "timeout": 초, "headers": {}, "request_timeout": 초}}
result: {"status": 마지막 HTTP 상태|null, "kind": null|"http"|"url"|"timeout"|"os", "reason": 문장, "detail": 오류 본문 앞부분}

같은 프로세스에서 블로킹으로 쓰려면 perform(spec) 을 직접 호출한다(단위 테스트·스모크)."""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def _write_body(spec: dict, data: bytes) -> None:
    out_file = spec.get("out_file")
    if out_file:
        with open(out_file, "wb") as f:
            f.write(data)


def _http_error(exc: urllib.error.HTTPError) -> dict:
    detail = ""
    try:
        detail = exc.read().decode("utf-8", "replace")[:400]
    except (OSError, ValueError):
        pass
    return {"status": exc.code, "kind": "http", "reason": str(exc.reason), "detail": detail}


def _open(url: str, method: str, headers: dict, data, timeout: float):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def _poll(spec: dict, first_body: bytes) -> dict:
    """202 응답의 job_id 로 결과 URL 을 폴링한다. 대기(pending_status)면 interval 뒤 재시도."""
    poll = spec["poll"]
    try:
        job = json.loads(first_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"status": 202, "kind": "os", "reason": "202 응답 본문이 JSON 이 아님", "detail": ""}
    job_id = job.get(poll.get("job_id_field", "job_id")) if isinstance(job, dict) else None
    if not job_id:
        return {"status": 202, "kind": "os", "reason": "서버가 job_id 없이 202를 보냈습니다", "detail": ""}
    url = poll["url_prefix"] + urllib.parse.quote(str(job_id))
    deadline = time.monotonic() + float(poll.get("timeout", 900))
    pending = int(poll.get("pending_status", 202))
    while True:
        with _open(url, "GET", poll.get("headers") or spec.get("headers"), None, float(poll.get("request_timeout", 60))) as resp:
            body = resp.read()
            if resp.status != pending:
                _write_body(spec, body)
                return {"status": resp.status, "kind": None, "reason": "", "detail": ""}
        if time.monotonic() > deadline:
            return {"status": pending, "kind": "timeout", "reason": "결과 대기 시간 초과 (%ds)" % int(poll.get("timeout", 900)), "detail": ""}
        time.sleep(float(poll.get("interval", 3.0)))


def perform(spec: dict) -> dict:
    """요청 한 건을 수행하고 결과 dict 를 돌려준다. 예외를 밖으로 내지 않는다."""
    data = None
    body_file = spec.get("body_file")
    try:
        if body_file:
            with open(body_file, "rb") as f:
                data = f.read()
        with _open(spec["url"], spec.get("method", "GET"), spec.get("headers"), data, float(spec.get("timeout", 300))) as resp:
            body = resp.read()
            status = resp.status
        if spec.get("poll") and status == int(spec["poll"].get("pending_status", 202)):
            return _poll(spec, body)
        _write_body(spec, body)
        return {"status": status, "kind": None, "reason": "", "detail": ""}
    except urllib.error.HTTPError as exc:
        return _http_error(exc)
    except urllib.error.URLError as exc:
        return {"status": None, "kind": "url", "reason": str(exc.reason), "detail": ""}
    except (OSError, ValueError) as exc:   # 소켓 타임아웃·파일 오류·디코딩 실패
        kind = "timeout" if "timed out" in str(exc).lower() else "os"
        return {"status": None, "kind": kind, "reason": str(exc), "detail": ""}


def main(argv) -> int:
    args = [a for a in argv if a != "--"]
    if not args:
        sys.stderr.write("사용: http_worker.py <spec.json>\n")
        return 2
    with open(args[-1], encoding="utf-8") as f:
        spec = json.load(f)
    result = perform(spec)
    with open(spec["result_file"], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
