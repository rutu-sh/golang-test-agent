import os
import subprocess
from typing import Tuple, Optional
from models import UsageInfo

from claude_agent_sdk import ( 
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock, ResultMessage
)

import logging


def read_system_prompt(filename: str = "system_prompt.md") -> str:
    """
    Reads the system prompt from a markdown file.

    Args:
        filename (str): The path to the system prompt file.

    Returns:
        str: The content of the system prompt file.
    """
    if not os.path.exists(filename):
        logging.warning(f"System prompt file {filename} not found. Using default prompt.")
        return ""

    with open(filename, 'r') as f:
        return f.read()


def run_tests_and_get_coverage(repo_path: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Run `go test -coverprofile=coverage.out` in the specified repository path
    Then run `go tools cover -func=coverage.out` to get coverage details. 

    Args:
        repo_path (str): The path to the Go repository.
    
    Returns:
        Tuple[bool, Optional[str], Optional[str]]: 
            A tuple containing a success flag, the path to the coverage file if successful, and an error message if any.
    """

    if repo_path.endswith('/'):
        repo_path = repo_path[:-1]

    result = subprocess.run(
        ["go", "test", "-coverprofile=coverage.out"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return False, None, f"Error running tests: {result.stdout}\n{result.stderr}"

    if not os.path.exists(f"{repo_path}/coverage.out"):
        return False, None, "Coverage output file does not exist."

    return True, f"{repo_path}/coverage.out", None


def get_total_coverage(repo_path: str) -> Tuple[bool, Optional[float], Optional[str]]:
    """
    Read the coverage.out file and extract total coverage percentage.

    Args:
        repo_path (str): The path to the Go repository.

    Returns:
        Tuple[bool, Optional[float], Optional[str]]:
            A tuple containing a success flag, the total coverage percentage if successful, and an error message if any.
    """

    try:

        success, coverage_file, error = run_tests_and_get_coverage(repo_path)
        if not success or coverage_file is None:
            return False, None, error

        res = subprocess.run(
            ["go", "tool", "cover", "-func=coverage.out", "-o=coverage_func.out"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )

        if res.returncode != 0:
            return False, None, "Error generating coverage function output."
        
        if not os.path.exists(f"{repo_path}/coverage_func.out"):
            return False, None, "Coverage function output file does not exist."
                
        coverage_file = f"{repo_path}/coverage_func.out"

        with open(coverage_file, 'r') as f:
            lines = f.readlines()

        total_coverage = 0.0

        for line in lines:
            if line.startswith("total:"):
                parts = line.split()
                if len(parts) >= 3:
                    total_coverage_str = parts[2].replace('%', '')
                    total_coverage = float(total_coverage_str)
                    break

    except Exception as e:
        return False, None, f"Exception occurred: {str(e)}"

    return True, total_coverage, None


def check_if_expected_blocks_covered(repo_path: str, filename: str, data: dict) -> Tuple[bool, bool, Optional[str]]:
    """
    Check if all expected blocks in the coverage data are covered.

    Args:
        repo_path (str): The path to the Go repository.
        filename (str): The name of the file to check.
        data (dict): The coverage data containing expected blocks.
    
    Returns:
        Tuple[bool, bool, Optional[str]]:
            A tuple containing a success flag, a flag indicating if all expected blocks are covered, and an error message if any.
    """

    try:

        go_mod_file = f"{repo_path}/go.mod"
        module_name = None

        with open(go_mod_file, 'r') as f:
            lines = f.readlines()

        for line in lines:
            if line.startswith("module"):
                module_name = line.split()[1].strip()
                break

        if module_name is None:
            return False, False, "Module name not found in go.mod"

        if not os.path.exists(f"{repo_path}/coverage.out"):
            return False, False, "Coverage output file does not exist."

        with open(f"{repo_path}/coverage.out", 'r') as f:
            lines = f.readlines()

        for line in lines[1:]:  # Skip the first line
            parts = line.split()
            if len(parts) < 3:
                continue 

            block = parts[0].split(':')[0]
            filename = block.replace(module_name, '').lstrip('/')
            block_statements = int(parts[1])
            is_covered = True if int(parts[2]) else False


            blocks_not_covered = []

            for expected_block in data['blocks']:
                block_info = parts[0].split(':')[-1]
                start = block_info.split(',')[0]
                end = block_info.split(',')[1]

                if start == expected_block['start'] and end == expected_block['end']:
                    if is_covered == 0:
                        blocks_not_covered.append({
                            "start": start,
                            "end": end
                        })

            if len(blocks_not_covered) == 0:
                return True, True, "All expected blocks are covered."

        return_str = f"After running the tests you generated for File: {filename}, the following uncovered blocks remain\nUncovered Blocks:\n"
        for block in blocks_not_covered:
            return_str += f"- Start: {block['start']}, End: {block['end']}\n"

        return True, False, return_str

    except Exception as e:
        return False, False, f"Exception occurred: {str(e)}"
    

def calculate_per_file_coverage(repo_path: str) -> Tuple[bool, Optional[dict], Optional[str]]:
    """
    Read the coverage_func.out file and calculate per-file coverage summary.
    output_file = f"{repo_path}/coverage_func.out"

    Args:
        repo_path (str): The path to the Go repository.

    Returns:
        Tuple[bool, Optional[dict], Optional[str]]:
            A tuple containing a success flag, a dictionary with per-file coverage data if successful, and an error message if any.
    """

    try:

        if repo_path.endswith('/'):
            repo_path = repo_path[:-1]

        input_file = f"{repo_path}/coverage.out"
        go_mod_file = f"{repo_path}/go.mod"

        if not os.path.exists(input_file):
            return False, None, "Coverage output file does not exist."
        if not os.path.exists(go_mod_file):
            return False, None, "go.mod file does not exist."

        # read the module name from go.mod
        with open(go_mod_file, 'r') as f:
            lines = f.readlines()

        module_name = None

        for line in lines:
            if line.startswith("module"):
                module_name = line.split()[1].strip()
                break
        
        if module_name is None:
            return False, None, "Module name not found in go.mod"
        
        file_coverage = {}

        with open(input_file, 'r') as f:
            lines = f.readlines()

        file_coverage = {}

        for line in lines[1:]:  # Skip the first line
            parts = line.split()
            if len(parts) < 3:
                continue 

            block = parts[0].split(':')[0]
            filename = block.replace(module_name, '').lstrip('/')
            block_statements = int(parts[1])
            is_covered = True if int(parts[2]) else False

            if filename not in file_coverage:
                file_coverage[filename] = {
                    "total_statements": 0,
                    "covered_statements": 0,
                    "coverage_percent": 0.0,
                    "blocks": []
                }

            block_info = parts[0].split(':')[-1]
            start = block_info.split(',')[0]
            end = block_info.split(',')[1]
            
            file_coverage[filename]["total_statements"] += block_statements

            if is_covered > 0:
                file_coverage[filename]["covered_statements"] += block_statements

            if file_coverage[filename]["total_statements"] > 0:
                file_coverage[filename]["coverage_percent"] = (
                    file_coverage[filename]["covered_statements"] / file_coverage[filename]["total_statements"]
                ) * 100.0
            
            file_coverage[filename]["blocks"].append({
                "start": start,
                "end": end,
                "is_covered": is_covered
            })
        
        return True, file_coverage, None

    except Exception as e:
        return False, None, f"Exception occurred: {str(e)}"


async def get_client(options: ClaudeAgentOptions) -> Tuple[bool, Optional[ClaudeSDKClient], Optional[str]]:
    """
    Initialize and return a ClaudeSDKClient based on the provided options.

    Args:
        options (ClaudeAgentOptions): The options for initializing the ClaudeSDKClient.
    
    Returns:
        Tuple[bool, Optional[ClaudeSDKClient], Optional[str]]:
            A tuple containing a success flag, the initialized ClaudeSDKClient if successful, and an error message if any.
    """

    try:
        client = ClaudeSDKClient(
            options=options
        )
        await client.connect()
    except Exception as e:
        return (False, None, f"Error initializing ClaudeSDKClient: {str(e)}")

    return (True, client, None)


async def generate_test_for_file(client: ClaudeSDKClient, repo_path: str, filename: str, data: dict) -> Tuple[bool, Optional[UsageInfo], Optional[str]]:
    """
    Use the ClaudeSDKClient to generate tests for a specific file based on coverage data.

    Args:
        client (ClaudeSDKClient): The Claude SDK client to use for generating tests.
        repo_path (str): The path to the Go repository.
        filename (str): The name of the file to generate tests for.
        data (dict): The coverage data containing uncovered blocks.
    
    Returns:
        Tuple[bool, Optional[UsageInfo], Optional[str]]:
            A tuple containing a success flag, usage information if successful, and an error message if any.
    """

    try:

        uncovered_blocks = [block for block in data['blocks'] if block['is_covered'] == 0]
        if not uncovered_blocks:
            return False, None, "No uncovered blocks found for the file."

        prompt = f"Write tests for uncovered lines in File: {filename}\nUncovered Blocks:\n"
        for block in uncovered_blocks:
            prompt += f"- Start: {block['start']}, End: {block['end']}\n"

        usage_info = UsageInfo(0, 0, 0)

        await client.query(prompt=prompt)

        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(f"Claude[{filename}]: {block.text}")
            elif isinstance(message, ResultMessage):
                print(f"[DONE] {filename}; {message.subtype}")
                if hasattr(message, "usage") and message.usage:
                    usage_info.input_tokens += message.usage["input_tokens"]
                    usage_info.output_tokens += message.usage["output_tokens"]
                    usage_info.total_tokens += message.usage["input_tokens"] + message.usage["output_tokens"]
    except Exception as e:
        return False, None, f"Exception occurred: {str(e)}"
        
    return True, usage_info, None


async def fix_test_errors(client: ClaudeSDKClient, repo_path: str, usage_info: UsageInfo, filename: str, errors: str) -> Tuple[bool, Optional[UsageInfo], Optional[str]]:
    """
    Use the ClaudeSDKClient to fix errors in the generated tests for a specific file.

    Args:
        client (ClaudeSDKClient): The Claude SDK client to use for fixing tests.
        repo_path (str): The path to the Go repository.
        usage_info (UsageInfo): The current usage information to update.
        filename (str): The name of the file with test errors.
        errors (str): The error messages from running the tests.
    
    Returns:
        Tuple[bool, Optional[UsageInfo], Optional[str]]:
            A tuple containing a success flag, updated usage information if successful, and an error message if any.
    """

    try:

        prompt = f"The tests you generated for File: {filename} have the following errors when run:\n{errors}\nPlease fix the tests accordingly."

        await client.query(prompt=prompt)

        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(f"Claude[Fix-{filename}]: {block.text}")
            elif isinstance(message, ResultMessage):
                print(f"[DONE Fix] {filename}; {message.subtype}")
                if hasattr(message, "usage") and message.usage:
                    usage_info.input_tokens += message.usage["input_tokens"]
                    usage_info.output_tokens += message.usage["output_tokens"]
                    usage_info.total_tokens += message.usage["input_tokens"] + message.usage["output_tokens"]
    except Exception as e:
        return False, None, f"Exception occurred: {str(e)}"
        
    return True, usage_info, None


def git_revert_file(repo_path: str, filename: str) -> Tuple[bool, Optional[str]]:
    """
    Revert changes to a specific file using git checkout.

    Args:
        repo_path (str): The path to the Go repository.
        filename (str): The name of the file to revert.

    Returns:
        Tuple[bool, Optional[str]]:
            A tuple containing a success flag and an error message if any.
    """

    try:

        result = subprocess.run(
            ["git", "checkout", "--", filename],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            return False, f"Error reverting file {filename}: {result.stderr}"

    except Exception as e:
        return False, f"Exception occurred while reverting file {filename}: {str(e)}"

    return True, None