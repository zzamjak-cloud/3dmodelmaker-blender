# Codex CLI (codex exec) 백엔드
import json
import os

from .base import AgentBackend, AgentReply

_LAST_MSG = "codex_last_message.txt"
REASONING_EFFORT = "high"  # 모델을 명시할 때만 함께 지정한다


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
        # 추론 강도도 사용자 설정(medium 등)에 맡기면 시트 비율을 재지 않고 일반형으로 찍는다
        if not self.model:
            return []
        return ["-m", self.model, "-c", f'model_reasoning_effort="{REASONING_EFFORT}"']

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
