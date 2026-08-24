#!/usr/bin/env python3
"""M0 CLI 계약 스파이크 — 애드온 밖(터미널)에서 CLI 동작을 검증한다.

사용법:
    python3 scripts/spike_cli.py claude   # claude -p 검증
    python3 scripts/spike_cli.py codex    # codex exec 검증

검증 항목: JSON 출력/세션 ID → resume 멀티턴 → 펜스 코드 블록 형식 준수
"""
import json
import os
import subprocess
import sys
import tempfile

PROMPT_1 = (
    "다음 형식으로만 답하라: 첫 줄 `STATUS: REVISE`, 이어서 python 코드 블록 1개. "
    "코드 내용은 print('hello') 한 줄."
)
PROMPT_2 = "직전에 준 코드에서 출력 문자열만 'world'로 바꿔 같은 형식으로 다시 답하라."


def run(cmd, cwd):
    print(f"\n$ {' '.join(cmd[:6])} ...")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300,
                            stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        print(f"[실패] 종료 코드 {result.returncode}\n{result.stderr[-1000:]}")
        sys.exit(1)
    return result.stdout


def check_format(text):
    ok_status = "STATUS:" in text
    ok_fence = "```python" in text or "```py" in text
    print(f"  STATUS 헤더: {'OK' if ok_status else '누락!'} / 코드 블록: {'OK' if ok_fence else '누락!'}")


def spike_claude(workdir):
    out = run(["claude", "-p", PROMPT_1, "--output-format", "json"], workdir)
    data = json.loads(out)
    session_id = data.get("session_id")
    print(f"  session_id: {session_id}")
    check_format(data.get("result", ""))

    out2 = run(["claude", "-p", PROMPT_2, "--output-format", "json",
                "--resume", session_id], workdir)
    data2 = json.loads(out2)
    check_format(data2.get("result", ""))
    print(f"  resume 후 'world' 포함: {'OK' if 'world' in data2.get('result', '') else '실패!'}")


def spike_codex(workdir):
    last = os.path.join(workdir, "last.txt")
    out = run(["codex", "exec", "--json", "-s", "read-only", "--skip-git-repo-check", "--cd", workdir,
               "-o", last, PROMPT_1], workdir)
    session_id = None
    for line in out.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        # thread.started 이벤트의 thread_id가 resume용 세션 ID
        session_id = session_id or event.get("thread_id") or event.get("session_id")
    print(f"  session_id: {session_id}")
    with open(last, encoding="utf-8") as f:
        check_format(f.read())

    # resume은 -s/--cd 미지원 (cwd는 프로세스 작업 디렉토리 사용)
    run(["codex", "exec", "resume", session_id, "--json", "--skip-git-repo-check",
         "-o", last, PROMPT_2], workdir)
    with open(last, encoding="utf-8") as f:
        text = f.read()
    check_format(text)
    print(f"  resume 후 'world' 포함: {'OK' if 'world' in text else '실패!'}")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "claude"
    with tempfile.TemporaryDirectory(prefix="lp3d_spike_") as tmp:
        (spike_claude if target == "claude" else spike_codex)(tmp)
    print("\n스파이크 통과")
