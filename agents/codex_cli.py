# Codex CLI (codex exec) 백엔드
import json
import os

from .base import AgentBackend, AgentReply

_LAST_MSG = "codex_last_message.txt"
REASONING_EFFORT = "high"  # 세션이 강도를 정하지 않고 모델만 명시했을 때의 기본값


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

    def _model_flags(self):
        # -m 을 resume에도 붙인다 — 빼면 ~/.codex/config.toml 기본 모델로 검토 턴이 돈다.
        # 추론 강도는 세션이 턴 역할별로 정한다(생성 high, 검토·수정 medium 등). 추론 토큰은
        # 출력 토큰으로 과금되므로 강도가 곧 비용이다. 모델도 강도도 없으면 CLI 설정을 따른다.
        flags = []
        if self.model:
            flags += ["-m", self.model]
        effort = self.reasoning_effort or (REASONING_EFFORT if self.model else "")
        if effort:
            flags += ["-c", f'model_reasoning_effort="{effort}"']
        return flags

    @staticmethod
    def _image_flags(images):
        # -i 는 값을 여러 개 받는 옵션이라 바로 뒤의 "-"(stdin 프롬프트)까지 이미지로 먹는다 —
        # 다른 플래그 앞에 둬서 다음 플래그가 값 목록을 끊게 한다
        flags = []
        for img in images or []:
            flags += ["-i", img]
        return flags

    def build_initial_command(self, user_prompt: str, images: list = None) -> list:
        # 프롬프트 인자는 "-" — codex exec가 stdin에서 읽는다 (.cmd 셸림 줄 잘림 회피)
        cmd = [self.exe, "exec", *self._image_flags(images), *self._model_flags(),
               *self._common_flags()]
        cmd.append("-")
        return cmd

    def build_resume_command(self, session_id: str, user_prompt: str, images: list) -> list:
        # resume 서브커맨드는 -s/--cd를 지원하지 않음 (cwd는 subprocess의 workdir 사용)
        cmd = [
            self.exe, "exec", "resume", session_id,
            *self._image_flags(images), *self._model_flags(),
            "--json", "--skip-git-repo-check",
            "-o", os.path.join(self.workdir, _LAST_MSG),
        ]
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
        return AgentReply(text=text, session_id=session_id, usage=self.parse_usage(stdout))

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

    def parse_usage(self, stdout: str):
        """{"type":"turn.completed","usage":{...}} 이벤트에서 토큰 사용량을 꺼낸다. 없으면 None.

        턴마다 어느 역할이 토큰을 쓰는지 로그에 남겨야 라우팅 설정의 효과를 확인할 수 있다."""
        usage = None
        for event in self._iter_events(stdout):
            if event.get("type") != "turn.completed":
                continue
            found = event.get("usage")
            if isinstance(found, dict):
                usage = {
                    "input_tokens": int(found.get("input_tokens", 0) or 0),
                    "cached_input_tokens": int(found.get("cached_input_tokens", 0) or 0),
                    "output_tokens": int(found.get("output_tokens", 0) or 0),
                }
        return usage

    def _last_agent_message(self, stdout: str) -> str:
        # {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
        text = ""
        for event in self._iter_events(stdout):
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text") or text
        return text
