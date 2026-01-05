# Claude Agent for Generating Unit Tests for Go Codebases

This agent is designed to assist developers in improving test coverage for their Go codebases by generating unit tests for uncovered lines of code. It leverages the capabilities of large language models to analyze uncovered code blocks and produce high-quality test functions.

## Features

- **Targeted Code Coverage**: Generates tests specifically for uncovered lines in Go files.
- **Incremental Coverage Improvement**: Identifies remaining uncovered lines after test execution and generates additional tests as needed.
- **Adherence to Go Conventions**: Ensures that generated tests follow Go programming conventions and best practices.
- **Targeted Fixes**: Can fix errors in previously generated tests based on user feedback.
- **State Management**: Utilizes a state machine to manage the workflow of analyzing code, generating tests, running tests, and fixing issues.


## Prerequisites

1. Install `uv` for python virtual environments [link](https://docs.astral.sh/uv/getting-started/installation/)
2. Install `go` programming language [link](https://go.dev/doc/install)
3. Set up a Go workspace with the codebase you want to improve test coverage for.
4. Generate Anthropic API Key [link](https://console.anthropic.com/dashboard)


## Usage

1. Clone the repository:

   ```bash
   git clone github.com/rutu-sh/golang-test-agent
   cd golang-test-agent
   ```

2. Create and activate a virtual environment using `uv`:

   ```bash
    uv venv .venv
    uv activate
    ```

3. Install the required dependencies:

    ```bash
    uv sync
    ```

4. Create a `.env` file in the `go-agent` directory and add your Anthropic API key:

   ```env
   ANTHROPIC_API_KEY=your_anthropic_api_key_here
   ```

5. CD into the `go-agent` directory:

   ```bash
   cd go-agent
   ```

6. Run the agent with the path to your Go codebase and desired target coverage percentage:

   ```bash
   uv run main.py --repo_path /path/to/your/go/codebase --target_coverage 80
   ```



