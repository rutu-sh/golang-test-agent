#!/usr/bin/env python3
"""
MCP Server for Go Test Coverage Agent

This server exposes tools for improving Go test coverage:
- run_coverage_analysis: Analyze current test coverage for a Go repository
- generate_tests: Generate tests for uncovered code in a specific file
- improve_coverage: Run the full coverage improvement workflow
"""

import asyncio
import logging
import os
from typing import Optional, Tuple
from dotenv import load_dotenv

import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("go-test-agent")

# System prompt for the agent
SYSTEM_PROMPT = """
You are a Go testing assistant specialized in achieving comprehensive test coverage.
Your task is to either write new tests or fix existing ones:

1. Context: User wants to improve test coverage for their Go codebase.
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

# Initialize the MCP server
server = Server("go-test-agent")


async def check_go_installed() -> Tuple[bool, Optional[str]]:
    """Check if Go is installed on the system."""
    try:
        import subprocess
        result = subprocess.run(
            ["go", "version"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False, "Go is not installed or not found in PATH."
        return True, None
    except Exception as e:
        return False, str(e)


async def validate_repo_path(repo_path: str) -> Tuple[bool, Optional[str]]:
    """Validate that the repository path exists and is a Go project."""
    if not os.path.exists(repo_path):
        return False, f"Repository path {repo_path} does not exist."

    go_mod_path = os.path.join(repo_path, "go.mod")
    if not os.path.exists(go_mod_path):
        return False, f"No go.mod file found in {repo_path}. Not a Go module."

    return True, None


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List available tools for Go test coverage improvement."""
    return [
        types.Tool(
            name="run_coverage_analysis",
            description="Analyze the current test coverage for a Go repository. Returns overall coverage percentage and per-file coverage details.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Absolute path to the Go repository",
                    },
                },
                "required": ["repo_path"],
            },
        ),
        types.Tool(
            name="get_uncovered_blocks",
            description="Get uncovered code blocks for a specific Go file. Returns blocks that need test coverage.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Absolute path to the Go repository",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Relative path to the Go file within the repository",
                    },
                },
                "required": ["repo_path", "file_path"],
            },
        ),
        types.Tool(
            name="improve_coverage",
            description="Run the full test coverage improvement workflow. This will iteratively generate tests for uncovered code until target coverage is reached or maximum iterations are exhausted.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Absolute path to the Go repository",
                    },
                    "target_coverage": {
                        "type": "number",
                        "description": "Target coverage percentage (0-100). Default is 100.",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "Maximum number of iterations. Default is 5.",
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                "required": ["repo_path"],
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """Handle tool execution requests."""

    if not arguments:
        raise ValueError("Missing arguments")

    # Check if Go is installed
    go_ok, go_error = await check_go_installed()
    if not go_ok:
        return [types.TextContent(type="text", text=f"Error: {go_error}")]

    if name == "run_coverage_analysis":
        repo_path = arguments.get("repo_path")
        if not repo_path:
            return [types.TextContent(type="text", text="Error: repo_path is required")]

        # Validate repository
        valid, error = await validate_repo_path(repo_path)
        if not valid:
            return [types.TextContent(type="text", text=f"Error: {error}")]

        # Get total coverage
        success, total_coverage, error = agent_utils.get_total_coverage(repo_path)
        if not success:
            return [types.TextContent(type="text", text=f"Error getting coverage: {error}")]

        # Get per-file coverage
        success, coverage_data, error = agent_utils.calculate_per_file_coverage(repo_path)
        if not success:
            return [types.TextContent(type="text", text=f"Error calculating per-file coverage: {error}")]

        # Format the response
        response = f"Total Coverage: {total_coverage:.2f}%\n\n"
        response += "Per-File Coverage:\n"

        for filename, data in sorted(coverage_data.items(), key=lambda x: x[1]["coverage_percent"]):
            uncovered = data["total_statements"] - data["covered_statements"]
            response += f"- {filename}: {data['coverage_percent']:.2f}% ({uncovered} uncovered statements)\n"

        return [types.TextContent(type="text", text=response)]

    elif name == "get_uncovered_blocks":
        repo_path = arguments.get("repo_path")
        file_path = arguments.get("file_path")

        if not repo_path or not file_path:
            return [types.TextContent(type="text", text="Error: repo_path and file_path are required")]

        # Validate repository
        valid, error = await validate_repo_path(repo_path)
        if not valid:
            return [types.TextContent(type="text", text=f"Error: {error}")]

        # Get per-file coverage
        success, coverage_data, error = agent_utils.calculate_per_file_coverage(repo_path)
        if not success:
            return [types.TextContent(type="text", text=f"Error calculating coverage: {error}")]

        if file_path not in coverage_data:
            return [types.TextContent(type="text", text=f"Error: File {file_path} not found in coverage data")]

        file_data = coverage_data[file_path]
        uncovered_blocks = [block for block in file_data["blocks"] if not block["is_covered"]]

        if not uncovered_blocks:
            return [types.TextContent(type="text", text=f"File {file_path} has 100% coverage!")]

        response = f"Uncovered blocks in {file_path}:\n"
        response += f"Total: {len(uncovered_blocks)} blocks\n\n"

        for i, block in enumerate(uncovered_blocks, 1):
            response += f"{i}. Start: {block['start']}, End: {block['end']}\n"

        return [types.TextContent(type="text", text=response)]

    elif name == "improve_coverage":
        repo_path = arguments.get("repo_path")
        target_coverage = arguments.get("target_coverage", 100.0)
        max_iterations = arguments.get("max_iterations", 5)

        if not repo_path:
            return [types.TextContent(type="text", text="Error: repo_path is required")]

        # Validate repository
        valid, error = await validate_repo_path(repo_path)
        if not valid:
            return [types.TextContent(type="text", text=f"Error: {error}")]

        # Check for API key
        if not os.getenv("ANTHROPIC_API_KEY"):
            return [types.TextContent(
                type="text",
                text="Error: ANTHROPIC_API_KEY environment variable is not set"
            )]

        # This would require the full agent logic
        # For now, return a message indicating this needs the Claude Agent SDK
        return [types.TextContent(
            type="text",
            text=f"Note: The improve_coverage tool requires the Claude Agent SDK to function.\n"
                 f"This MCP server currently only provides coverage analysis tools.\n\n"
                 f"To run the full improvement workflow, use the main.py script directly:\n"
                 f"  python main.py\n\n"
                 f"Repository: {repo_path}\n"
                 f"Target Coverage: {target_coverage}%\n"
                 f"Max Iterations: {max_iterations}"
        )]

    else:
        raise ValueError(f"Unknown tool: {name}")


@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    """List available prompts for test generation."""
    return [
        types.Prompt(
            name="generate_tests",
            description="Generate tests for uncovered code blocks in a Go file",
            arguments=[
                types.PromptArgument(
                    name="file_path",
                    description="Path to the Go file",
                    required=True,
                ),
                types.PromptArgument(
                    name="uncovered_blocks",
                    description="Comma-separated list of uncovered blocks (format: start-end)",
                    required=True,
                ),
            ],
        ),
    ]


@server.get_prompt()
async def handle_get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    """Generate prompts for test generation."""

    if name != "generate_tests":
        raise ValueError(f"Unknown prompt: {name}")

    if not arguments:
        raise ValueError("Missing arguments")

    file_path = arguments.get("file_path")
    uncovered_blocks = arguments.get("uncovered_blocks", "")

    if not file_path:
        raise ValueError("file_path is required")

    # Parse uncovered blocks
    blocks = []
    if uncovered_blocks:
        for block_str in uncovered_blocks.split(","):
            parts = block_str.strip().split("-")
            if len(parts) == 2:
                blocks.append({"start": parts[0], "end": parts[1]})

    # Build the prompt
    prompt_text = SYSTEM_PROMPT + "\n\n---\n\n"
    prompt_text += f"Write tests for uncovered lines in File: {file_path}\n"
    prompt_text += "Uncovered Blocks:\n"

    for block in blocks:
        prompt_text += f"- Start: {block['start']}, End: {block['end']}\n"

    return types.GetPromptResult(
        description=f"Generate tests for {file_path}",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=prompt_text),
            )
        ],
    )


async def main():
    """Run the MCP server."""
    # Load environment variables
    load_dotenv()

    # Run the server using stdin/stdout streams
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="go-test-agent",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
