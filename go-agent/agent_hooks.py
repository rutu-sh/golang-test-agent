import logging

logging.basicConfig(level=logging.INFO)

async def pre_write_hook(input_data, tool_use_id, context) -> dict:
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
