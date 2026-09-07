#!/usr/bin/env python3
"""터미널에서 GPT-6 Astra의 단일 생성 응답 계약을 검증한다.

사용법: python3 scripts/spike_cli.py
실제 Codex CLI를 호출하므로 로그인과 모델 접근 권한이 필요하다.
"""
import os
import subprocess
import tempfile


def main():
    prompt = (
        "다음 형식으로만 답하라: 첫 줄 `STATUS: DONE`, 이어서 python 코드 블록 1개. "
        "코드 내용은 print('hello') 한 줄."
    )
    with tempfile.TemporaryDirectory(prefix="lp3d_spike_") as workdir:
        last = os.path.join(workdir, "last.txt")
        result = subprocess.run(
            ["codex", "exec", "--json", "-m", "gpt-6-astra", "-s", "read-only",
             "--skip-git-repo-check", "--cd", workdir, "-o", last, "-"],
            cwd=workdir, input=prompt, capture_output=True, text=True, timeout=300,
        )
        if result.returncode:
            raise SystemExit(f"CLI 실패 ({result.returncode}): {result.stderr[-1000:]}")
        with open(last, encoding="utf-8") as stream:
            reply = stream.read()
        if "STATUS: DONE" not in reply or "```python" not in reply:
            raise SystemExit("응답 형식 실패: DONE 헤더 또는 Python 코드 블록 누락")
    print("Astra 단일 생성 계약 검증 통과")


if __name__ == "__main__":
    main()
