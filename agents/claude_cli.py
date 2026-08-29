# Claude Code CLI (claude -p) 백엔드
import json
import os

from .base import AgentBackend, AgentReply

_SYS_FILENAME = "system_prompt.md"


class ClaudeBackend(AgentBackend):
    name = "claude"

    def prepare_workdir(self, system_prompt: str):
        with open(os.path.join(self.workdir, _SYS_FILENAME), "w", encoding="utf-8") as f:
            f.write(system_prompt)

    def _common_flags(self):
        return [
            "--output-format", "json",
            "--allowedTools", "Read",  # 캡처 이미지 열람만 허용
            "--max-turns", "8",
        ]

    def _model_flags(self, for_critique=False) -> list:
        # 비평 턴은 빠른 모델로 분리 가능 (--resume에도 --model이 적용됨)
        model = (self.critique_model if for_critique else "") or self.model
        return ["--model", model] if model else []

    def build_initial_command(self, user_prompt: str, images: list = None) -> list:
        # 프롬프트 인자를 생략하면 claude -p가 stdin에서 읽는다 (.cmd 셸림 줄 잘림 회피)
        # 이미지는 image_prompt_hint + Read 도구로 전달되므로 플래그 불필요
        return [
            self.exe, "-p",
            "--append-system-prompt-file", os.path.join(self.workdir, _SYS_FILENAME),
            *self._model_flags(),
            *self._common_flags(),
        ]

    def build_resume_command(self, session_id: str, user_prompt: str, images: list) -> list:
        # 이미지는 workdir 상대경로를 프롬프트에 명시하고 Read 도구로 읽게 한다
        return [
            self.exe, "-p",
            "--resume", session_id,
            *self._model_flags(for_critique=bool(images)),
            *self._common_flags(),
        ]

    def image_prompt_hint(self, images: list) -> str:
        if not images:
            return ""
        rels = [os.path.basename(p) for p in images]
        return (
            "\n\n다음 이미지 파일들을 Read 도구로 반드시 모두 읽고 확인하라 "
            f"(현재 작업 디렉토리 기준): {', '.join(rels)}"
        )

    def parse_response(self, stdout: str) -> AgentReply:
        data = json.loads(stdout)
        # --output-format json: {"result": "...", "session_id": "...", ...}
        return AgentReply(text=data.get("result", ""), session_id=data.get("session_id"))
