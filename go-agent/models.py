from dataclasses import dataclass
from typing import Optional
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient


IMPROVEMENT_THRESHOLD = 2.0  # Minimum coverage improvement percentage to consider progress
MAX_STATE_LOOP_ITERATIONS = 500 # Max iterations to prevent infinite loops


class UsageInfo:
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __init__(self, input_tokens: int, output_tokens: int, total_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens

@dataclass
class MachineStateGlobals:
    repo_path: str
    target_coverage: float
    options: ClaudeAgentOptions
    usage: UsageInfo = UsageInfo(0, 0, 0)

@dataclass
class MachineStateStart(MachineStateGlobals):
    state_info: str = "Starting Go Test Coverage Improvement Agent"
    state_str: str = "START"

    def __str__(self) -> str:
        return f"[{self.state_str}] {self.state_info} | Repo: {self.repo_path} | Target Coverage: {self.target_coverage}%"

@dataclass
class MachineStateAnalyze(MachineStateGlobals):
    state_info: str = "Analyzing current test coverage"
    state_str: str = "ANALYZE"
    n_iterations_left: int = 5
    current_coverage: float = -1 * IMPROVEMENT_THRESHOLD - 0.1  # to ensure first iteration proceeds
    last_coverage: float = -1 * IMPROVEMENT_THRESHOLD - 0.1

    def __str__(self) -> str:
        return f"[{self.state_str}] {self.state_info} | Target Coverage: {self.target_coverage}% | Current Coverage: {self.current_coverage}% | Iterations Left: {self.n_iterations_left}"

@dataclass
class MachineStateSelectFile(MachineStateGlobals):
    state_info: str = "Selecting file for test generation"
    state_str: str = "SELECT_FILE"
    current_coverage: float = 0.0
    coverage_data: dict = None
    file_queue: list[str] = None

    def __str__(self) -> str:
        return f"[{self.state_str}] {self.state_info} | Target Coverage: {self.target_coverage}% | Current Coverage: {self.current_coverage}% | Files Left: {len(self.file_queue) if self.file_queue else 0}"

@dataclass
class MachineStateGenerateTest(MachineStateGlobals):
    state_info: str = "Generating tests for selected file"
    state_str: str = "GENERATE_TEST"
    filename: str = ""
    data: Optional[dict] = None
    client: Optional[ClaudeSDKClient] = None

    def __str__(self) -> str:
        return f"[{self.state_str}] {self.state_info} | File: {self.filename}"

@dataclass
class MachineStateRunTest(MachineStateGlobals):
    state_info: str = "Running tests for selected file"
    state_str: str = "RUN_TEST"
    last_file_generated: str = ""
    n_fixes_left: int = 3
    data: Optional[dict] = None
    client: Optional[ClaudeSDKClient] = None

    def __str__(self) -> str:
        return f"[{self.state_str}] {self.state_info} | File: {self.last_file_generated} | Fixes Left: {self.n_fixes_left}"

@dataclass
class MachineStateFixing(MachineStateGlobals):
    state_info: str = "Fixing failing tests"
    state_str: str = "FIXING"
    filename: str = ""
    errors: str = ""
    data: Optional[dict] = None
    client: Optional[ClaudeSDKClient] = None

    def __str__(self) -> str:
        return f"[{self.state_str}] {self.state_info} | File: {self.filename} | Errors: {repr(self.errors)}"

@dataclass
class MachineStateError(MachineStateGlobals):
    state_info: str = "Error encountered"
    state_str: str = "ERROR"
    error_message: str = ""

    def __str__(self) -> str:
        return f"[{self.state_str}] {self.state_info} | Error Message: {repr(self.error_message)}"

@dataclass
class MachineStateTerminate(MachineStateGlobals):
    state_info: str = "Terminating agent"
    state_str: str = "TERMINATE"
    coverage_achieved: float = 0.0
    successful_termination: bool = False
    errors: Optional[str] = None

    def __str__(self) -> str:
        return f"[{self.state_str}] {self.state_info} | Coverage Achieved: {self.coverage_achieved}% | Successful: {self.successful_termination} | Errors: {repr(self.errors if self.errors else 'None')}"


MachineState = (
    MachineStateStart
    | MachineStateAnalyze
    | MachineStateSelectFile
    | MachineStateGenerateTest
    | MachineStateRunTest
    | MachineStateFixing
    | MachineStateError
    | MachineStateTerminate
)


class Machine:
    state: MachineState

    allowed_transitions = {
        "START": ["ANALYZE", "ERROR"],
        "ANALYZE": ["SELECT_FILE", "TERMINATE", "ERROR"],
        "SELECT_FILE": ["GENERATE_TEST", "ANALYZE", "TERMINATE", "ERROR"],
        "GENERATE_TEST": ["RUN_TEST", "TERMINATE", "ERROR"],
        "RUN_TEST": ["SELECT_FILE", "TERMINATE", "FIXING", "ERROR"],
        "FIXING": ["RUN_TEST", "TERMINATE", "ERROR"],
        "ERROR": [],
        "TERMINATE": [],
    }

    def __init__(self, repo_path: str, target_coverage: float, options: ClaudeAgentOptions):
        self.state = MachineStateStart(repo_path=repo_path, target_coverage=target_coverage, options=options)

    def can_transition(self, new_state: MachineState) -> bool:
        current_state_str = self.state.state_str
        new_state_str = new_state.state_str
        return new_state_str in self.allowed_transitions[current_state_str]

    def transition(self, new_state: MachineState):
        current_state_str = self.state.state_str
        new_state_str = new_state.state_str

        if self.can_transition(new_state):
            self.state = new_state
            print(f"\n\n\nTransitioned from {current_state_str} to {new_state_str}")
        else:
            raise ValueError(f"Invalid transition from {current_state_str} to {new_state_str}")


