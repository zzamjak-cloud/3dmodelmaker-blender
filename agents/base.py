# CLI 에이전트 백엔드 추상화
from abc import ABC, abstractmethod


class AgentReply:
    def __init__(self, text: str, session_id: str = None):
        self.text = text
        self.session_id = session_id


class AgentBackend(ABC):
    """claude/codex CLI 차이를 흡수하는 어댑터.

    사용 규약:
    - build_initial_command: 새 세션 시작 (시스템 프롬프트 포함)
    - build_resume_command: 기존 세션 이어가기 (이미지 첨부 가능)
    - parse_response: stdout(+workdir 부산물)에서 응답 텍스트와 세션 ID 추출

    프롬프트는 명령줄 인자가 아니라 **stdin**으로 전달한다. Windows의 npm 설치본은
    codex.cmd/claude.cmd 배치 셸림을 통해 실행되는데, 배치 파일은 명령줄을 줄 단위로
    처리하므로 여러 줄 인자가 첫 줄에서 잘린다(오류 traceback·비평 프롬프트 유실).
    따라서 build_*_command는 프롬프트를 포함하지 않으며, session이 stdin으로 넘긴다.
    """

    name = "base"

    def __init__(self, exe_path: str, workdir: str):
        self.exe = exe_path
        self.workdir = workdir

    @abstractmethod
    def prepare_workdir(self, system_prompt: str):
        """시스템 프롬프트를 백엔드 방식에 맞게 workdir에 배치."""

    @abstractmethod
    def build_initial_command(self, user_prompt: str) -> list:
        ...

    @abstractmethod
    def build_resume_command(self, session_id: str, user_prompt: str, images: list) -> list:
        ...

    @abstractmethod
    def parse_response(self, stdout: str) -> AgentReply:
        ...

    def image_prompt_hint(self, images: list) -> str:
        """이미지를 프롬프트 텍스트로 전달해야 하는 백엔드용 힌트. 기본은 빈 문자열."""
        return ""
