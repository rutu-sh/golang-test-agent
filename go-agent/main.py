import os 
import asyncio
import logging
import subprocess
from dotenv import load_dotenv
from typing import Optional, Tuple

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

import agent_utils
from models import (
    MachineStateStart,
    MachineStateAnalyze,
    MachineStateSelectFile,
    MachineStateGenerateTest,
    MachineStateRunTest,
    MachineStateFixing,
    MachineStateTerminate,
    MachineStateError,
    Machine,
    IMPROVEMENT_THRESHOLD,
    MAX_STATE_LOOP_ITERATIONS,
)


system_prompt = """
You are a Go testing assistant specialized in achieving comprehensive test coverage.
Your task is to either write new tests or fix existing ones:

1.  Context: User wants to improve test coverage for thier Go codebase.
    Prompt: Write tests for uncovered lines in File: /path/to/file.go
    Input Format:
            File: /path/to/file.go
            Uncovered Blocks:
            - Start: 10.1, End: 15.5
            - Start: 30.2, End: 35.7
    Explanation:
        The Start and End indicate line.column positions of uncovered code blocks.
    Task:
        Based on the provided context and prompt, generate Go test functions that specifically target the uncovered lines

2. Context: User wants to fix errors in generated tests.
   Prompt: The tests you generated for File: /path/to/file.go have the following errors when run:
            [Error details here]
            Please fix the tests accordingly.
    Explanation:
        You will be provided with error messages from running the tests you generated.
    Task:
        Based on the provided context and prompt, fix the errors. 
    
Requirements:

1. Coverage Focus:

- Write tests ONLY for the specified uncovered lines
- Each test must execute the uncovered code paths
- Ensure all branches, error cases, and edge cases in uncovered blocks are tested


2. Test File Handling:

- If test file exists: append new tests to it
- If test file doesn't exist: create <original_filename>_test.go with correct package name
- Never modify existing test functions


3. Test Quality:

- Use table-driven tests where appropriate
- Test all conditional branches (if/else, switch cases)
- Test error returns and edge cases
- Use meaningful test function names: Test<FunctionName>_<Scenario>
- Include necessary setup and teardown
- Mock external dependencies if needed


4. Go Conventions:

- Import "testing" package
- Use t.Run() for subtests
- Use t.Error/t.Fatal appropriately
- Follow standard Go formatting

5. Output:

- Provide ONLY the Go test code
- No explanations, comments, or summaries
- No markdown formatting or code blocks
- Ready to write directly to test file


# Prohibited Actions:

- Do not run go test
- Do not generate commands for running tests
- Do not generate coverage reports or HTML
- Do not execute any commands
- Do not provide analysis or explanations
- Do not modify covered code
- Do not write tests for already covered lines
- Do not provide summaries or commentary
- Do not provide output in any format other than raw Go code
"""


async def pre_write_hook(input_data, tool_use_id, context):
    """
    PreToolUse hook for Write operations.
    Checks if the file being written is a _test.go file.

    Args:
        input_data: Event details including tool_name, tool_input, etc.
        tool_use_id: ID to correlate PreToolUse and PostToolUse events
        context: Hook context (unused in this implementation)
    """
    # Check if this is a PreToolUse event
    if input_data['hook_event_name'] != 'PreToolUse':
        return {}

    # Check if the tool is Write
    if input_data['tool_name'] == 'Write':
        # Get the file path from tool input
        file_path = input_data['tool_input'].get('file_path', '')

        # Check if it's a _test.go file
        if file_path.endswith('_test.go'):
            logging.info(f"PreToolUse (ID: {tool_use_id}): Writing to test file: {file_path} - ALLOWED")
            # Allow the operation to proceed
            return {}
        else:
            logging.warning(f"PreToolUse (ID: {tool_use_id}): Attempted to write to non-test file: {file_path} - DENIED")
            # Deny write operations to non-test files
            return {
                'hookSpecificOutput': {
                    'hookEventName': input_data['hook_event_name'],
                    'permissionDecision': 'deny',
                    'permissionDecisionReason': f'Write operation blocked: Only _test.go files can be written. File: {file_path}'
                }
            }

    return {}


async def pre_bash_hook(input_data, tool_use_id, context):
    """
    PreToolUse hook for Bash operations.
    Blocks any bash commands that attempt to run tests or generate coverage reports.

    Args:
        input_data: Event details including tool_name, tool_input, etc.
        tool_use_id: ID to correlate PreToolUse and PostToolUse events
        context: Hook context (unused in this implementation)
    """
    # Check if this is a PreToolUse event
    if input_data['hook_event_name'] != 'PreToolUse':
        return {}

    # Check if the tool is Bash
    if input_data['tool_name'] == 'Bash':
        # Get the command from tool input
        command = input_data['tool_input'].get('command', '')

        # Block commands that run tests or generate coverage reports
        prohibited_keywords = ['go test', 'coverprofile', 'coverage.out', 'go tool cover']
        if any(keyword in command for keyword in prohibited_keywords):
            logging.warning(f"PreToolUse (ID: {tool_use_id}): Attempted to run prohibited bash command: {command} - DENIED")
            # Deny the operation
            return {
                'hookSpecificOutput': {
                    'hookEventName': input_data['hook_event_name'],
                    'permissionDecision': 'deny',
                    'permissionDecisionReason': f'Bash command blocked: Running tests or generating coverage reports is not allowed. Command: {command}'
                }
            }
        else:
            logging.info(f"PreToolUse (ID: {tool_use_id}): Running allowed bash command: {command} - ALLOWED")

    return {}


async def init(repo_path: str) -> Tuple[bool, Optional[str]]:
    try:

        # load env from .env file if exists
        load_dotenv()

        # check if go is installed
        result = subprocess.run(
            ["go", "version"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False, "Go is not installed or not found in PATH."

        if not os.path.exists(repo_path):
            return False, f"Repository path {repo_path} does not exist."

        if not os.getenv("ANTHROPIC_API_KEY"):
            return False, "Anthropic API key not set in environment variables."

    except Exception as e:
        return False, e.__str__()

    return True, None


async def main():

    repo_path = "/Users/rutu/stuff/vscode-workspaces/spectro/stats"
    # repo_path = "/Users/rutu/stuff/vscode-workspaces/spectro/golang-test-agent/calc"

    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
        permission_mode="acceptEdits",
        cwd=repo_path,
        hooks={
            'PreToolUse': [
                HookMatcher(matcher='Write', hooks=[pre_write_hook]),
                HookMatcher(matcher='Bash', hooks=[pre_bash_hook]),
            ]
        }
    )

    is_initialized, error_message = await init(repo_path)

    if not is_initialized:
        print(f"Initialization failed: {error_message}")
        return
    
    # instantiate the state machine
    MACHINE = Machine(repo_path=repo_path, target_coverage=100.0, options=options)

    # state stack to allow backtracking
    STATE_STACK = []

    # state_history to log all states
    STATE_HISTORY = []

    # start the state machine loop
    i = 0

    while MACHINE.state.state_str != "TERMINATE" and MACHINE.state.state_str != "ERROR" and i < MAX_STATE_LOOP_ITERATIONS:

        current_state = MACHINE.state

        STATE_STACK.append(current_state)
        STATE_HISTORY.append(current_state)

        i += 1

        if isinstance(current_state, MachineStateStart):
            print(current_state.state_info)
            new_state = MachineStateAnalyze(
                repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                options=current_state.options, usage=current_state.usage
            )
            MACHINE.transition(new_state)
            continue

        elif isinstance(current_state, MachineStateAnalyze):
            print(current_state.state_info)

            success, current_coverage, error = agent_utils.get_total_coverage(current_state.repo_path)

            # handle errors in getting coverage
            if not success or current_coverage is None:
                logging.error(f"Error getting total coverage: {error}")
                new_state = MachineStateError(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, error_message=error, usage=current_state.usage
                )
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(new_state)
                continue

            current_state.last_coverage = current_state.current_coverage
            current_state.current_coverage = current_coverage

            # check termination conditions
            if current_state.current_coverage >= current_state.target_coverage:
                print("c1")
                logging.info(f"Target coverage {current_state.target_coverage}% achieved with current coverage {current_coverage}%. Terminating.")
                new_state = MachineStateTerminate(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, coverage_achieved=current_coverage, successful_termination=True,
                    usage=current_state.usage
                )
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(new_state)
                continue

            # no more iterations left
            if current_state.n_iterations_left <= 0:
                print("c2")
                logging.info(f"Maximum iterations reached without achieving target coverage {current_state.target_coverage}%. Terminating.")
                new_state = MachineStateTerminate(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, coverage_achieved=current_coverage,
                    successful_termination=False, errors="Maximum iterations reached without achieving target coverage.",
                    usage=current_state.usage
                )
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(new_state)
                continue

            # decrement the number of iterations left
            current_state.n_iterations_left -= 1

            if (current_state.current_coverage - current_state.last_coverage) < IMPROVEMENT_THRESHOLD:
                print("c3")
                print(f"Coverage improvement below threshold of {IMPROVEMENT_THRESHOLD}%. Terminating. Current coverage: {current_state.current_coverage}%, Last coverage: {current_state.last_coverage}%")
                logging.info(f"Coverage improvement below threshold of {IMPROVEMENT_THRESHOLD}%. Terminating. Current coverage: {current_state.current_coverage}%, Last coverage: {current_state.last_coverage}%") 
                new_state = MachineStateTerminate(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, coverage_achieved=current_state.current_coverage,
                    successful_termination=False,
                    errors="Coverage improvement below threshold.", usage=current_state.usage
                )
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(new_state)
                continue

            success, coverage_data, error = agent_utils.calculate_per_file_coverage(current_state.repo_path)

            if not success or coverage_data is None:
                logging.info(f"Error calculating per-file coverage: {error}")
                print(error)
                new_state = MachineStateError(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, error_message=error, usage=current_state.usage
                )
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(new_state)
                continue

            file_queue = sorted(coverage_data.keys(), key=lambda f: (coverage_data[f]["total_statements"] - coverage_data[f]["covered_statements"]), reverse=True)


            new_state = MachineStateSelectFile(
                repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                options=current_state.options, coverage_data=coverage_data,
                file_queue=file_queue, current_coverage=current_coverage,
                usage=current_state.usage
            )

            MACHINE.transition(new_state)
            continue

        elif isinstance(current_state, MachineStateSelectFile):
            print(current_state.state_info)

            success, current_coverage, error = agent_utils.get_total_coverage(current_state.repo_path)

            # handle errors in getting coverage
            if not success or current_coverage is None:
                logging.error(f"Error getting total coverage: {error}")
                new_state = MachineStateError(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, error_message=error, usage=current_state.usage
                )
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(new_state)
                continue

            current_state.last_coverage = current_state.current_coverage
            current_state.current_coverage = current_coverage

            # check termination conditions
            if current_state.current_coverage >= current_state.target_coverage:
                print("c1")
                logging.info(f"Target coverage {current_state.target_coverage}% achieved with current coverage {current_coverage}%. Terminating.")
                new_state = MachineStateTerminate(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, coverage_achieved=current_coverage, successful_termination=True,
                    usage=current_state.usage
                )
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(new_state)
                continue

            # if all files were processed, go back to analyze state
            if not current_state.file_queue:

                last_analyze_state = None

                while STATE_STACK:
                    last_analyze_state = STATE_STACK.pop()
                    if not isinstance(last_analyze_state, MachineStateAnalyze):
                        continue
                    break

                if not isinstance(last_analyze_state, MachineStateAnalyze):
                    new_state = MachineStateError(
                        repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                        options=current_state.options, error_message="State stack corrupted.",
                    )
                    MACHINE.transition(new_state)
                    STATE_STACK.append(new_state)
                    STATE_HISTORY.append(new_state)
                    continue

                new_state = last_analyze_state
                new_state.usage = current_state.usage
                MACHINE.transition(new_state)

                continue

            next_file = current_state.file_queue.pop(0)

            new_state = MachineStateGenerateTest(
                repo_path=current_state.repo_path, target_coverage=current_state.target_coverage,
                options=current_state.options, filename=next_file,
                data=current_state.coverage_data[next_file], usage=current_state.usage
            )

            MACHINE.transition(new_state)
            continue

        elif isinstance(current_state, MachineStateGenerateTest):
            print(current_state.state_info)

            success, client, error = await agent_utils.get_client(current_state.options)

            if not success or client is None:
                new_state = MachineStateError(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage,
                    options=current_state.options, error_message=error, usage=current_state.usage
                )
                MACHINE.transition(new_state)

                STATE_HISTORY.append(new_state)
                continue

            current_state.client = client

            success, usage_info, error = await agent_utils.generate_test_for_file(
                client=current_state.client, repo_path=current_state.repo_path,
                filename=current_state.filename, data=current_state.data,
            )

            if not success:

                print(f"Error generating tests for file {current_state.filename}: {error}")

                new_state = MachineStateError(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage,
                    options=current_state.options, error_message=error, usage=current_state.usage
                )

                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(new_state)
                continue

            current_state.usage.input_tokens += usage_info.input_tokens
            current_state.usage.output_tokens += usage_info.output_tokens
            current_state.usage.total_tokens += usage_info.total_tokens

            new_state = MachineStateRunTest(
                repo_path=current_state.repo_path, target_coverage=current_state.target_coverage,
                options=current_state.options, last_file_generated=current_state.filename,
                usage=current_state.usage, client=current_state.client
            )

            MACHINE.transition(new_state)
            continue

        elif isinstance(current_state, MachineStateRunTest):
            print(current_state.state_info)

            success, coverage_file, error = agent_utils.run_tests_and_get_coverage(current_state.repo_path)

            if not success and current_state.n_fixes_left > 0:
                current_state.n_fixes_left -= 1
                new_state = MachineStateFixing(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage,
                    options=current_state.options, filename=current_state.last_file_generated,
                    errors=error, client=current_state.client, usage=current_state.usage
                )
                MACHINE.transition(new_state)
                continue

            elif not success and current_state.n_fixes_left <= 0:
                logging.error(f"Max fixes reached for file {current_state.last_file_generated}. Moving on.")
                agent_utils.git_revert_file(current_state.repo_path, current_state.last_file_generated)
                
            while STATE_STACK:
                last_select_file_state = STATE_STACK.pop()
                if not isinstance(last_select_file_state, MachineStateSelectFile):
                    continue
                break

            if not isinstance(last_select_file_state, MachineStateSelectFile):
                new_state = MachineStateError(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, error_message="State stack corrupted.", 
                    usage=current_state.usage
                )
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(new_state)
                continue

            new_state = last_select_file_state
            new_state.usage = current_state.usage
            MACHINE.transition(new_state)
            continue 

        elif isinstance(current_state, MachineStateFixing):
            print(current_state.state_info)

            print(f"Fixing errors in tests for file {current_state.filename}:\n{current_state.errors}")

            success, usage_info, error = await agent_utils.fix_test_errors(
                client=current_state.client, repo_path=current_state.repo_path,
                usage_info=current_state.usage, filename=current_state.filename,
                errors=current_state.errors
            )

            current_state.usage.input_tokens += usage_info.input_tokens
            current_state.usage.output_tokens += usage_info.output_tokens
            current_state.usage.total_tokens += usage_info.total_tokens

            if not success:
                new_state = MachineStateError(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage,
                    options=current_state.options, error_message=error, usage=current_state.usage
                )
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(new_state)
                continue

            while STATE_STACK:
                last_run_test_state = STATE_STACK.pop()
                if not isinstance(last_run_test_state, MachineStateRunTest):
                    continue
                break

            if not isinstance(last_run_test_state, MachineStateRunTest):
                new_state = MachineStateError(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, error_message="State stack corrupted.", 
                    usage=current_state.usage
                )
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(new_state)
                continue

            new_state = last_run_test_state
            new_state.usage = current_state.usage
            MACHINE.transition(new_state)
            continue
        else:
            print(f"Unknown state encountered: {current_state.state_str}")
            new_state = MachineStateError(
                repo_path=current_state.repo_path, target_coverage=current_state.target_coverage,
                options=current_state.options, error_message="Unknown state encountered.",
                usage=current_state.usage
            )
            MACHINE.transition(new_state)
            STATE_STACK.append(new_state)
            STATE_HISTORY.append(new_state)
            break

    success, current_coverage, error = agent_utils.get_total_coverage(current_state.repo_path)
    if success and current_coverage is not None:
        final_coverage = current_coverage
    else:
        final_coverage = -1.0

    print("\n\n=== AGENT TERMINATED ===")
    print(f"Final Coverage Achieved: {final_coverage}%")
    print("\tToken Usage")
    print(f"Input Tokens: {MACHINE.state.usage.input_tokens}")
    print(f"Output Tokens: {MACHINE.state.usage.output_tokens}")
    print(f"Total Tokens: {MACHINE.state.usage.total_tokens}")

    print("\n\n=== STATE HISTORY ===")
    for idx, state in enumerate(STATE_HISTORY):
        print(f"{idx+1}. {state.state_str}")


if __name__ == "__main__":
    asyncio.run(main())

