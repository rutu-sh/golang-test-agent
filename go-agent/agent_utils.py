import os
import subprocess
from typing import Tuple, Optional
from models import UsageInfo

from claude_agent_sdk import ( 
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock, ResultMessage
)


def run_tests_and_get_coverage(repo_path: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Run `go test -coverprofile=coverage.out` in the specified repository path
    Then run `go tools cover -func=coverage.out` to get coverage details. 
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


def calculate_per_file_coverage(repo_path: str) -> Tuple[bool, Optional[dict], Optional[str]]:
    """
    Read the coverage_func.out file and calculate per-file coverage summary.
    output_file = f"{repo_path}/coverage_func.out"
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