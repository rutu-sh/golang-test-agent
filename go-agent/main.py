import os
import asyncio
import argparse
import logging
import copy
import subprocess
from dotenv import load_dotenv
from typing import Optional, Tuple

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

import agent_utils
import agent_hooks
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

logging.basicConfig(level=logging.INFO)

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


async def main(repo_path: str, target_coverage: float):

    system_prompt = agent_utils.read_system_prompt()

    logging.info(f"Starting Go Test Coverage Agent for repository: {repo_path}, Target Coverage: {target_coverage}%")

    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
        permission_mode="acceptEdits",
        cwd=repo_path,
        hooks={
            'PreToolUse': [
                HookMatcher(matcher='Write', hooks=[agent_hooks.pre_write_hook]),
                HookMatcher(matcher='Bash', hooks=[agent_hooks.pre_bash_hook]),
            ]
        },
        system_prompt=system_prompt
    )

    is_initialized, error_message = await init(repo_path)

    if not is_initialized:
        logging.error(f"Initialization failed: {error_message}")
        return
    
    # instantiate the state machine
    MACHINE = Machine(repo_path=repo_path, target_coverage=target_coverage, options=options)

    # state stack to allow backtracking
    STATE_STACK = []

    # state_history to log all states
    STATE_HISTORY = []

    # start the state machine loop
    i = 0

    while MACHINE.state.state_str != "TERMINATE" and MACHINE.state.state_str != "ERROR" and i < MAX_STATE_LOOP_ITERATIONS:

        current_state = MACHINE.state

        STATE_STACK.append(current_state)
        STATE_HISTORY.append(copy.copy(current_state))

        i += 1

        if isinstance(current_state, MachineStateStart):
            logging.info(f"[{current_state.state_str}] : {current_state.state_info}")

            new_state = MachineStateAnalyze(
                repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                options=current_state.options, usage=current_state.usage
            )

            logging.info(current_state)
            logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")
            MACHINE.transition(new_state)
            continue

        elif isinstance(current_state, MachineStateAnalyze):
            logging.info(f"[{current_state.state_str}] : {current_state.state_info}")

            success, current_coverage, error = agent_utils.get_total_coverage(current_state.repo_path)

            # handle errors in getting coverage
            if not success or current_coverage is None:
                logging.error(f"[{current_state.state_str}] : Error getting total coverage - {repr(error)}")
                new_state = MachineStateError(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, error_message=error, usage=current_state.usage
                )

                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")               
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(copy.copy(new_state))
                continue

            current_state.last_coverage = current_state.current_coverage
            current_state.current_coverage = current_coverage

            # check termination conditions
            if current_state.current_coverage >= current_state.target_coverage:

                logging.info(f"[{current_state.state_str}] : Target coverage {current_state.target_coverage}% achieved with current coverage {current_coverage}%. Terminating.")

                new_state = MachineStateTerminate(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, coverage_achieved=current_coverage, successful_termination=True,
                    usage=current_state.usage
                )

                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")               
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(copy.copy(new_state))
                continue

            # no more iterations left
            if current_state.n_iterations_left <= 0:
                logging.info(f"[{current_state.state_str}] : Maximum iterations reached without achieving target coverage {current_state.target_coverage}%. Terminating.")
                new_state = MachineStateTerminate(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, coverage_achieved=current_coverage,
                    successful_termination=False, errors="Maximum iterations reached without achieving target coverage.",
                    usage=current_state.usage
                )

                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")               
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(copy.copy(new_state))
                continue

            # decrement the number of iterations left
            current_state.n_iterations_left -= 1

            if (current_state.current_coverage - current_state.last_coverage) < IMPROVEMENT_THRESHOLD:
                logging.info(f"[{current_state.state_str}] : Coverage improvement below threshold of {IMPROVEMENT_THRESHOLD}%. Terminating."
                            f" Current coverage: {current_state.current_coverage}%, Last coverage: {current_state.last_coverage}%") 

                new_state = MachineStateTerminate(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, coverage_achieved=current_state.current_coverage,
                    successful_termination=False,
                    errors="Coverage improvement below threshold.", usage=current_state.usage
                )

                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")               
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(copy.copy(new_state))
                continue

            success, coverage_data, error = agent_utils.calculate_per_file_coverage(current_state.repo_path)

            if not success or coverage_data is None:
                logging.info(f"[{current_state.state_str}] : Error calculating per-file coverage: {repr(error)}")

                new_state = MachineStateError(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, error_message=error, usage=current_state.usage
                )

                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")               
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(copy.copy(new_state))
                continue

            file_queue = sorted(coverage_data.keys(), key=lambda f: (coverage_data[f]["total_statements"] - coverage_data[f]["covered_statements"]), reverse=True)


            new_state = MachineStateSelectFile(
                repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                options=current_state.options, coverage_data=coverage_data,
                file_queue=file_queue, current_coverage=current_coverage,
                usage=current_state.usage
            )

            logging.info(current_state)
            logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")               
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
                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")               
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(copy.copy(new_state))
                continue

            current_state.last_coverage = current_state.current_coverage
            current_state.current_coverage = current_coverage

            # check termination conditions
            if current_state.current_coverage >= current_state.target_coverage:
                logging.info(f"Target coverage {current_state.target_coverage}% achieved with current coverage {current_coverage}%. Terminating.")
                new_state = MachineStateTerminate(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage, 
                    options=current_state.options, coverage_achieved=current_coverage, successful_termination=True,
                    usage=current_state.usage
                )

                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(copy.copy(new_state))
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

                    logging.info(current_state)
                    logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")
                    MACHINE.transition(new_state)
                    STATE_STACK.append(new_state)
                    STATE_HISTORY.append(copy.copy(new_state))
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

            logging.info(current_state)
            logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")
            MACHINE.transition(new_state)
            continue

        elif isinstance(current_state, MachineStateGenerateTest):
            logging.info(f"[{current_state.state_str}] : {current_state.state_info}")

            success, client, error = await agent_utils.get_client(current_state.options)

            if not success or client is None:
                new_state = MachineStateError(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage,
                    options=current_state.options, error_message=error, usage=current_state.usage
                )

                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")
                MACHINE.transition(new_state)

                STATE_HISTORY.append(copy.copy(new_state))
                continue

            current_state.client = client

            success, usage_info, error = await agent_utils.generate_test_for_file(
                client=current_state.client, repo_path=current_state.repo_path,
                filename=current_state.filename, data=current_state.data,
            )

            if not success:

                logging.error(f"Error generating tests for file {current_state.filename}: {repr(error)}")

                new_state = MachineStateError(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage,
                    options=current_state.options, error_message=error, usage=current_state.usage
                )


                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")

                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(copy.copy(new_state))
                continue

            current_state.usage.input_tokens += usage_info.input_tokens
            current_state.usage.output_tokens += usage_info.output_tokens
            current_state.usage.total_tokens += usage_info.total_tokens

            new_state = MachineStateRunTest(
                repo_path=current_state.repo_path, target_coverage=current_state.target_coverage,
                options=current_state.options, last_file_generated=current_state.filename,
                usage=current_state.usage, client=current_state.client, data=current_state.data
            )

            logging.info(current_state)
            logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")
            MACHINE.transition(new_state)
            continue

        elif isinstance(current_state, MachineStateRunTest):
            logging.info(f"[{current_state.state_str}] : {current_state.state_info}")

            success, coverage_file, error = agent_utils.run_tests_and_get_coverage(current_state.repo_path)

            if not success and current_state.n_fixes_left > 0:
                current_state.n_fixes_left -= 1
                new_state = MachineStateFixing(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage,
                    options=current_state.options, filename=current_state.last_file_generated,
                    errors=error, client=current_state.client, usage=current_state.usage, 
                    data=current_state.data
                )
                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")
                MACHINE.transition(new_state)
                continue

            success, all_expected_blocks_covered, error = agent_utils.check_if_expected_blocks_covered(
                repo_path=current_state.repo_path, filename=current_state.last_file_generated, 
                data=current_state.data
            )

            if not all_expected_blocks_covered and current_state.n_fixes_left > 0:
                logging.error(f"Expected uncovered blocks in file {current_state.last_file_generated} are still not covered after running tests.")
                current_state.n_fixes_left -= 1
                new_state = MachineStateFixing(
                    repo_path=current_state.repo_path, target_coverage=current_state.target_coverage,
                    options=current_state.options, filename=current_state.last_file_generated,
                    errors="Expected uncovered blocks are still not covered after running tests.", 
                    client=current_state.client, usage=current_state.usage,
                    data=current_state.data
                )

                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")
                MACHINE.transition(new_state)
                continue

            if not success and current_state.n_fixes_left <= 0:
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

                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(new_state)
                continue

            new_state = last_select_file_state
            new_state.usage = current_state.usage
            MACHINE.transition(new_state)
            continue 

        elif isinstance(current_state, MachineStateFixing):
            logging.info(f"[{current_state.state_str}] : {current_state.state_info}")

            logging.info(f"Fixing errors for file {current_state.filename}: {repr(current_state.errors)}")

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

                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")
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

                logging.info(current_state)
                logging.info(f"[{current_state.state_str}] : Transitioning to new state: {new_state.state_str}")
                MACHINE.transition(new_state)
                STATE_STACK.append(new_state)
                STATE_HISTORY.append(new_state)
                continue

            new_state = last_run_test_state
            new_state.usage = current_state.usage
            MACHINE.transition(new_state)
            continue
        else:
            logging.info(f"Unknown state encountered")
            new_state = MachineStateError(
                repo_path=current_state.repo_path, target_coverage=current_state.target_coverage,
                options=current_state.options, error_message="Unknown state encountered.",
                usage=current_state.usage
            )

            logging.info(current_state)
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
        print(f"{idx+1}. {state}")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Go Test Coverage Agent")
    parser.add_argument(
        "--repo-path",
        type=str,
        required=True,
        help="Absolute Path to the Go repository to analyze.",
    )
    parser.add_argument(
        "--target-coverage",
        type=float,
        required=True,
        help="Target code coverage percentage to achieve. (<= 100)",
    )

    args = parser.parse_args()

    repo_path = args.repo_path
    target_coverage = args.target_coverage

    if target_coverage < 0.0 or target_coverage > 100.0:
        logging.error("Target coverage must be between 0 and 100.")
        raise ValueError(f"Target coverage must be between 0 and 100. Value provided: {target_coverage}")

    asyncio.run(main(repo_path=repo_path, target_coverage=target_coverage))


