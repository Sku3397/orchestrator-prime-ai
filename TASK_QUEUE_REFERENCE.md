# Task Queue Reference (`task_queue.json`)

This document details the structure and provides examples for tasks submitted to the Cursor Bridge Module via `task_queue.json`. All tasks are expected to be part of a list under the root key `"tasks"`.

## Core Task Structure

Each task object in the `tasks` list generally follows this structure (defined by Pydantic models in `models.py`):

```json
{
    "task_id": "unique-task-identifier-001",
    "status": "pending", // See Status Lifecycle section
    "instruction_details": { ... }, // Specific to the task type
    "agent_action_details": { ... }, // Populated by cursor_bridge.py for the agent
    "results": { ... }, // Populated by the agent and/or cursor_bridge.py
    "history": [ ... ], // Log of status changes and key events
    "notes": "Optional notes about the task",
    "last_updated": "<ISO_TIMESTAMP>"
}
```

-   **`task_id`**: A unique string identifying the task.
-   **`status`**: The current state of the task (e.g., `pending`, `pending_agent_action`, `completed_by_agent`, `failed_by_agent`, `completed`, `failed`).
-   **`instruction_details`**: An object containing the primary instructions from the Meta-Agent. Its content varies by task type.
-   **`agent_action_details`**: Populated by `cursor_bridge.py`. Contains the specific tool call and parameters for the downstream agent to execute.
-   **`results`**: Populated by the agent after execution and potentially by `cursor_bridge.py` during finalization.
-   **`history`**: A list of timestamped events tracking the task's progress.
-   **`notes`**: Free-text notes.
-   **`last_updated`**: ISO timestamp of the last modification to the task object.

## Task Status Lifecycle

1.  **`pending`**: Initial state when a Meta-Agent adds a task.
2.  **`pending_agent_action`**: `cursor_bridge.py` has picked up the task and prepared `agent_action_details` for the agent.
3.  **`processing_by_agent`** (Optional): Agent might set this status upon starting execution.
4.  **`completed_by_agent`**: Agent has successfully executed the action described in `agent_action_details`.
5.  **`failed_by_agent`**: Agent encountered an error during execution.
6.  **`completed`**: `cursor_bridge.py` has successfully processed a `completed_by_agent` task (e.g., archived logs, finalized results) and pruned it from the active queue.
7.  **`failed`**: `cursor_bridge.py` has processed a `failed_by_agent` task or encountered an error during its own processing/archival phase, and then pruned it.

## Task Types & `instruction_details` Examples

### 1. File Modification: `replace_content`

Replaces the entire content of a file.

**`instruction_details`:**
```json
{
    "instruction": "Replace the entire content of the specified file.",
    "files_to_modify": [
        {
            "file_path": "target_files/replace_me.txt",
            "action": "replace_content",
            "content": "This is the new content that will overwrite the entire file."
        }
    ]
}
```

**`agent_action_details` (example populated by `cursor_bridge.py`):**
```json
{
    "tool_to_call": "edit_file",
    "target_file": "target_files/replace_me.txt",
    "code_edit": "This is the new content that will overwrite the entire file.",
    "instructions": "Replace the entire content of the file as per task <task_id>."
}
```

### 2. File Modification: `append_content`

Appends content to the end of a file.

**`instruction_details`:**
```json
{
    "instruction": "Append text to the end of my_document.txt.",
    "files_to_modify": [
        {
            "file_path": "docs/my_document.txt",
            "action": "append_content",
            "content": "\nThis line will be added to the end."
        }
    ]
}
```

**`agent_action_details` (example populated by `cursor_bridge.py`):**
```json
{
    "tool_to_call": "edit_file",
    "target_file": "docs/my_document.txt",
    "instructions": "Append content to the end of the file.",
    "content_to_append": "\nThis line will be added to the end." 
    // Agent will need to read existing content and construct the new full content.
}
```

### 3. File Modification: `insert_before_line`

Inserts content before a specific line marker.

**`instruction_details`:**
```json
{
    "instruction": "Insert new configuration before the deployment marker.",
    "files_to_modify": [
        {
            "file_path": "config/settings.yaml",
            "action": "insert_before_line",
            "line_marker": "# END_OF_USER_CONFIG",
            "content_to_insert": "  new_setting: true\n  another_setting: value"
        }
    ]
}
```

**`agent_action_details` (example populated by `cursor_bridge.py`):**
```json
{
    "tool_to_call": "edit_file",
    "target_file": "config/settings.yaml",
    "instructions": "Insert content before line containing '# END_OF_USER_CONFIG'.",
    "line_marker": "# END_OF_USER_CONFIG",
    "content_to_insert": "  new_setting: true\n  another_setting: value"
    // Agent constructs the `code_edit` string like: "// ... existing ...\n<content_to_insert>\n<line_marker>\n// ... existing ..."
}
```

### 4. File Modification: `insert_after_line` (Conceptual - Assuming similar structure)

Inserts content after a specific line marker.

**`instruction_details`:**
```json
{
    "instruction": "Insert logging statement after initialization.",
    "files_to_modify": [
        {
            "file_path": "src/main_app.py",
            "action": "insert_after_line",
            "line_marker": "self.initialize_components()",
            "content_to_insert": "        logger.info(\"Components initialized.\")"
        }
    ]
}
```

**`agent_action_details` would be similar to `insert_before_line`, with the agent adjusting logic for `code_edit` construction.**

### 5. File Modification: `delete_lines`

Deletes a range of lines (inclusive).

**`instruction_details`:**
```json
{
    "instruction": "Remove deprecated functions from utils.py, lines 50 to 75.",
    "files_to_modify": [
        {
            "file_path": "lib/utils.py",
            "action": "delete_lines",
            "start_line_number": 50,
            "end_line_number": 75
        }
    ]
}
```

**`agent_action_details` (example populated by `cursor_bridge.py`):**
```json
{
    "tool_to_call": "edit_file",
    "target_file": "lib/utils.py",
    "instructions": "Delete lines 50 through 75 from the file.",
    "start_line_number": 50,
    "end_line_number": 75
    // Agent constructs the `code_edit` string like: "// ... up to line 49 ...\n// ... from line 76 ..."
}
```

### 6. File Modification: `replace_lines`

Replaces a range of lines (inclusive) with new content.

**`instruction_details`:**
```json
{
    "instruction": "Update the configuration block in settings.py (lines 10-12).",
    "files_to_modify": [
        {
            "file_path": "config/settings.py",
            "action": "replace_lines",
            "start_line_number": 10,
            "end_line_number": 12,
            "replacement_content": "NEW_HOST = \"prod.server.com\"\nNEW_PORT = 8080"
        }
    ]
}
```

**`agent_action_details` (example populated by `cursor_bridge.py`):**
```json
{
    "tool_to_call": "edit_file",
    "target_file": "config/settings.py",
    "instructions": "Replace lines 10 through 12 with new content.",
    "start_line_number": 10,
    "end_line_number": 12,
    "replacement_content": "NEW_HOST = \"prod.server.com\"\nNEW_PORT = 8080"
    // Agent constructs `code_edit` string like: "// ... up to line 9 ...\n<replacement_content>\n// ... from line 13 ..."
}
```

### 7. Command Execution: `execute_commands_sequentially`

Executes one or more shell commands sequentially using `robust_terminal_handler.py` (RTH).

**`instruction_details`:**
```json
{
    "instruction": "Run build and test scripts.",
    "commands_to_execute": [
        {
            "command_id": "build_app",
            "command_string": "python setup.py build",
            "working_directory": "./app_module",
            "rth_timeout_config": {
                "total_timeout": 300,
                "launch_timeout": 30,
                "activity_timeout": 120
            },
            "outputs_to_capture": {
                 "stdout_file": "build_stdout.txt",
                 "stderr_file": "build_stderr.txt",
                 "status_file": "build_rth_status.json"
            }
        },
        {
            "command_id": "run_tests",
            "command_string": "pytest -v",
            "working_directory": ".",
            "rth_timeout_config": { "total_timeout": 600 }
        }
    ]
}
```

**`agent_action_details` (example populated by `cursor_bridge.py`):**
```json
{
    "tool_to_call": "execute_commands_sequentially",
    "commands_list": [ /* Same as commands_to_execute above */ ],
    "rth_config": {
        "rth_script_path": "C:/Users/Man/CursorAgentUtils/robust_terminal_handler.py", // Example path
        "python_exe_for_rth": "C:/Users/Man/AutoAgent/.venv/Scripts/python.exe" // Example path
    },
    "per_command_rth_outputs_base_dir": "instructions/test-cmd-exec-001/rth_outputs", // Example path
    "instructions": "Execute commands sequentially using RTH as specified."
}
```

**Agent `results` (example populated by agent after execution):**
```json
{
    "command_executions": [
        {
            "command_id": "build_app",
            "command_string": "python setup.py build",
            "status": "SUCCESS_BY_AGENT", // or FAILED_BY_AGENT
            "rth_status_file_content": { /* JSON content of RTH status file */ },
            "stdout_content": "...build output...",
            "stderr_content": ""
        },
        {
            "command_id": "run_tests",
            // ... similar fields ...
        }
    ],
    "overall_status_message": "All commands completed.",
    "final_task_status": "completed_by_agent"
}
```

This reference should help in understanding and creating tasks for the Cursor Bridge Module. For the precise Pydantic model definitions, refer to `models.py`. 