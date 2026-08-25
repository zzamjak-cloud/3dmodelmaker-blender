# Codex CLI (codex exec) 백엔드
import json
import os

from .base import AgentBackend, AgentReply

_LAST_MSG = "codex_last_message.txt"


class CodexBackend(AgentBackend):
    name = "codex"

    def prepare_workdir(self, system_prompt: str):
        # codex exec는 --cd 디렉토리의 AGENTS.md를 시스템 지침으로 읽는다
        with open(os.path.join(self.workdir, "AGENTS.md"), "w", encoding="utf-8") as f:
            f.write(system_prompt)

    def _common_flags(self):
        return [
            "--json",
            "-s", "read-only",  # 코드 생성만 필요하므로 읽기 전용 샌드박스
            "--skip-git-repo-check",  # 세션 workdir는 git 저장소가 아님
            "--cd", self.workdir,
            "-o", os.path.join(self.workdir, _LAST_MSG),
        ]

    def build_initial_command(self, user_prompt: str) -> list:
        # 프롬프트 인자는 "-" — codex exec가 stdin에서 읽는다 (.cmd 셸림 줄 잘림 회피)
        return [self.exe, "exec", *self._common_flags(), "-"]

    def build_resume_command(self, session_id: str, user_prompt: str, images: list) -> list:
        # resume 서브커맨드는 -s/--cd를 지원하지 않음 (cwd는 subprocess의 workdir 사용)
        cmd = [
            self.exe, "exec", "resume", session_id,
            "--json", "--skip-git-repo-check",
            "-o", os.path.join(self.workdir, _LAST_MSG),
        ]
        for img in images or []:
            cmd += ["-i", img]  # codex는 네이티브 이미지 첨부 지원
        cmd.append("-")  # 프롬프트는 stdin
        return cmd

    def parse_response(self, stdout: str) -> AgentReply:
        session_id = self._find_session_id(stdout)
        # 최종 메시지는 -o 파일이 가장 신뢰도 높음, 실패 시 JSONL 이벤트에서 복원
        text = ""
        last_path = os.path.join(self.workdir, _LAST_MSG)
        if os.path.isfile(last_path):
            with open(last_path, encoding="utf-8") as f:
                text = f.read()
        if not text:
            text = self._last_agent_message(stdout)
        return AgentReply(text=text, session_id=session_id)

    @staticmethod
    def _iter_events(stdout: str):
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

    def _find_session_id(self, stdout: str):
        # {"type":"thread.started","thread_id":"..."} 이벤트에서 추출
        for event in self._iter_events(stdout):
            for key in ("thread_id", "session_id"):
                value = event.get(key)
                if isinstance(value, str):
                    return value
        return None

    def _last_agent_message(self, stdout: str) -> str:
        # {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
        text = ""
        for event in self._iter_events(stdout):
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text") or text
        return text
