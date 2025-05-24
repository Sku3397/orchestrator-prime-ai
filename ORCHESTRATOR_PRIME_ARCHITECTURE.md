# Orchestrator Prime: System Architecture & Core Logic

**Overall Goal:** Orchestrator Prime acts as an AI Dev Manager. It uses the Gemini API as its "brain" to generate instructions for a "Dev Engineer" (simulated by the Cursor IDE agent working on a target project like Ledger CFO). Communication with the Dev Engineer is via file I/O (`dev_instructions/next_step.txt` and `dev_logs/cursor_step_output.txt`). The user interacts with Orchestrator Prime via a terminal interface.

**Key Modules & Responsibilities:**

*   **`main.py` (Terminal UI & Main Loop):**
    *   Handles user input from the terminal.
    *   Parses user commands (e.g., `project add`, `project select`, `goal`, `input`, `status`, `stop`, `quit`, `help`).
    *   Drives the `OrchestrationEngine`.
    *   Displays status, Gemini communications, and prompts for user input directly in the console.
    *   Initializes and shuts down the engine.

*   **`engine.py` (OrchestrationEngine Class):**
    *   The core state machine of the application.
    *   Manages the active project (`self.active_project`) and its state (`self.active_project_state`).
    *   **States (from `EngineState` Enum):** `IDLE`, `LOADING_PROJECT`, `PROJECT_SELECTED`, `RUNNING_CALLING_GEMINI`, `RUNNING_WAITING_LOG`, `PROCESSING_LOG`, `PAUSED_WAITING_USER_INPUT`, `SUMMARIZING_CONTEXT`, `TASK_COMPLETE`, and various `ERROR_*` states.
    *   **Workflow Methods:**
        *   `set_active_project(project)`: Loads project state.
        *   `start_task(initial_user_instruction)`: Initiates a new task sequence with Gemini. Includes initial project structure overview if it's the first call for the task.
        *   `resume_with_user_input(user_response)`: Sends user's response to Gemini when in `PAUSED_WAITING_USER_INPUT`.
        *   `_handle_log_file_created(log_file_path)`: Processes `cursor_step_output.txt`.
        *   `_handle_gemini_response(response_dict)`: Parses Gemini's response and transitions state.
        *   `_perform_summarization_if_needed()`: Manages context summarization.
        *   `_write_instruction_to_file(instruction)`: Writes to `dev_instructions/next_step.txt`.
        *   `start_watcher()` / `stop_watcher()`: Manages `watchdog` for `dev_logs`.
        *   `_on_cursor_timeout()`: Handles timeout if Cursor log isn't received.
        *   `stop_task()` / `shutdown()`: Graceful termination.
    *   Communicates back to `main.py` by updating its own state attributes (`self.state`, `self.pending_user_question`, `self.last_error_message`), which `main.py` polls and displays.

*   **`gemini_comms.py` (GeminiCommunicator Class):**
    *   Handles all authenticated API calls to the Google Gemini API.
    *   Constructs detailed prompts for Gemini, incorporating: Overall Project Goal, Initial Project Structure Overview (on first call), Context Summary, Recent Conversation History, and the latest Cursor Log.
    *   Parses Gemini's responses to identify instructions, requests for user input (`NEED_USER_INPUT:`), task completion (`TASK_COMPLETE`), or system errors (`SYSTEM_ERROR:`).
    *   Includes logic for `summarize_text()`.
    *   Handles API errors.

*   **`persistence.py`:**
    *   Manages saving and loading of:
        *   Project list: `app_data/projects.json` (list of `Project` objects).
        *   Individual project states: `{project_workspace_root}/.orchestrator_state/state.json` (contains `ProjectState` object for that project, including conversation history, context summary, etc.).
    *   Handles file I/O, JSON serialization/deserialization, and related errors.

*   **`config_manager.py` (ConfigManager Class):**
    *   Reads configuration from `config.ini` (e.g., Gemini API key, Gemini model name, default directory names).
    *   Provides methods to access configuration values.

*   **`models.py`:**
    *   Defines core data structures using Python dataclasses:
        *   `Project` (name, workspace_root_path, overall_goal, project_id).
        *   `Turn` (sender, message, timestamp) for conversation history.
        *   `ProjectState` (project_id, current_status, conversation_history, context_summary, last_instruction_to_cursor, max_history_turns, max_context_tokens, summarization_interval, gemini_turns_since_last_summary, etc.).
    *   Defines the `EngineState` Enum.

*   **`test_terminal_app.py`:**
    *   The primary test script using Python's `subprocess` module to run `main.py`.
    *   Simulates user terminal input and "Cursor Agent" file I/O.
    *   Contains the mocking infrastructure for `gemini_comms.py` (`MOCK_GEMINI_COMMS_TEMPLATE`, `apply_gemini_comms_mock`, `restore_gemini_comms_original`).

**File-Based Communication with "Dev Engineer" (Cursor IDE Agent):**
*   Instructions from Orchestrator Prime (Gemini): Written to `{project_workspace}/dev_instructions/next_step.txt`.
*   Log/Report from Dev Engineer: Written to `{project_workspace}/dev_logs/cursor_step_output.txt`. Orchestrator Prime's file watcher monitors this file.

## Cursor Bridge Module: Architecture & Logic

This module operates somewhat independently of the Orchestrator Prime Core's LLM loop. It's designed for direct task delegation from a Meta-Agent (or a testing framework) to a Cursor-like agent via a structured task queue.

**Overall Goal:** To enable an external system to submit specific, well-defined tasks (e.g., "replace lines 5-10 in file X with new content Y", "run command Z") for execution, monitor their progress, and retrieve results.

**Key Components & Responsibilities:**

*   **`task_queue.json` (Central Task Queue):**
    *   The primary communication channel. It's a JSON file, typically containing a list of task objects under a "tasks" key.
    *   Each task object is defined by Pydantic models in `models.py` (e.g., `Task`, `InstructionDetails`, `FileModificationInstruction`, `CommandExecutionInstruction`, `AgentActionDetails`).
    *   Tasks progress through various statuses: `pending` -> `pending_agent_action` -> `completed_by_agent` / `failed_by_agent` -> `completed` / `failed` (after archival/pruning by `cursor_bridge.py`).
    *   Holds all information necessary for `cursor_bridge.py` and the target agent to understand and execute the task.

*   **`cursor_bridge.py` (Task Processor & Delegator):**
    *   **Core Function:** Continuously polls `task_queue.json` for tasks with `status: "pending"`.
    *   **Task Preparation:** When a pending task is found, it typically:
        1.  Updates the task's status in `task_queue.json` to `pending_agent_action`.
        2.  Populates an `agent_action_details` block within the task object. This block contains specific instructions for the *agent* to execute, including:
            *   `tool_to_call`: e.g., `"edit_file"`, `"execute_commands_sequentially"`.
            *   Tool-specific parameters derived from the original `instruction_details` (e.g., `target_file`, `code_edit` content, `line_marker`, `start_line_number`, `commands_list`, `rth_config`).
    *   **Execution (Intended):** While `cursor_bridge.py` prepares the task for an agent, the actual execution of the `agent_action_details` is performed by a separate agent process that also monitors `task_queue.json`. (In the context of recent tests, I, the AI assistant, have been acting as this agent).
    *   **Post-Agent Processing:** After the agent updates the task to `completed_by_agent` or `failed_by_agent`, `cursor_bridge.py` is responsible for:
        1.  Final logging.
        2.  Archiving relevant files (task JSON, logs) to the `instructions/archive/processed/` or `instructions/archive/failed/` directories.
        3.  Updating the task status to `completed` or `failed`.
        4.  Pruning the finalized task from the active `task_queue.json`.
    *   **Logging:** Writes general operational logs to `cursor_bridge.log` and detailed task-specific logs/outputs to files within the `instructions/` directory (e.g., `<task_id>.output.log`).
    *   **Stability:** *Currently, this script faces significant stability issues, frequently hanging or timing out before completing its processing cycle.*

*   **`rth_local_copy.py` (Robust Process Runner):**
    *   A local copy of `robust_terminal_handler.py`.
    *   Used to launch `cursor_bridge.py` as a subprocess with enhanced reliability features: process monitoring, configurable timeouts (launch, activity, total), and structured status reporting to a JSON file.
    *   This is the recommended way to invoke `cursor_bridge.py` to manage its execution and capture its outcome, especially given its current instability.

*   **`models.py` (Data Structures for Cursor Bridge):**
    *   Extends its role from the Orchestrator Prime Core to define Pydantic models critical for the Cursor Bridge module. These ensure data consistency and provide clear schemas for:
        *   `Task`: The overall task structure in `task_queue.json`.
        *   `InstructionDetails`: Contains the Meta-Agent's original request (e.g., what file to modify, what command to run).
        *   `FileModificationInstruction`: Specific fields for various file actions (`action` type, `file_path`, `content`, `line_marker`, `start_line_number`, `end_line_number`, `replacement_content`, `content_to_insert`).
        *   `CommandExecutionInstruction`: Specific fields for command execution (`command_id`, `command_string`, `working_directory`, `rth_timeout_config`, `outputs_to_capture`).
        *   `AgentActionDetails`: The translated instructions prepared by `cursor_bridge.py` for the agent to execute (tool name and its parameters).
        *   Various status enums and supporting models.

*   **`instructions/` Directory:**
    *   Serves as the operational workspace for `cursor_bridge.py`.
    *   `cursor_bridge.log`: Main log file for the bridge.
    *   `<task_id>.output.log`, `<task_id>.error.json`: Per-task outputs and error reports.
    *   `archive/processed/` & `archive/failed/`: Storage for completed/failed task data (copied task JSON, logs).

*   **`templates/` Directory:**
    *   Contains JSON schema files that can be used to validate the structure of `task_queue.json` entries and other JSON artifacts used by the Cursor Bridge module.

**Data Flow & Task Lifecycle (Cursor Bridge Module):**

1.  **Task Creation:** A Meta-Agent (or test script) crafts a new task object according to the Pydantic models and adds it to the `tasks` list in `task_queue.json` with `status: "pending"`.
    *   For **file modifications**, `instruction_details` will contain `file_path`, `action` (e.g., `replace_content`, `append_content`), and action-specific parameters like `content`, `line_marker`, etc.
    *   For **command executions**, `instruction_details` will contain a list of `commands_to_execute`, each with `command_string`, `working_directory`, RTH configurations, etc.

2.  **Bridge - Task Pickup & Preparation:** `cursor_bridge.py` (running via `rth_local_copy.py`) polls `task_queue.json`.
    *   Detects a `"pending"` task.
    *   Updates the task's status in `task_queue.json` to `"pending_agent_action"`.
    *   Populates `agent_action_details` in the task object:
        *   For file modifications, this typically means `tool_to_call: "edit_file"` and parameters like `target_file`, `code_edit` (which might be the raw content for `replace_content`, or constructed by the agent for line-based operations), `line_marker`, etc.
        *   For command execution, this means `tool_to_call: "execute_commands_sequentially"` and parameters like `commands_list`, `rth_config` (paths to RTH script and Python exe), `per_command_rth_outputs_base_dir`.

3.  **Agent - Execution:** A separate agent process (simulated by the AI assistant in recent tests) monitors `task_queue.json`.
    *   Detects a task with `status: "pending_agent_action"`.
    *   Parses `agent_action_details`.
    *   Executes the specified tool with the provided arguments (e.g., calls the `edit_file` API, or uses RTH to run commands).
    *   Upon completion, updates the task's status in `task_queue.json` to `"completed_by_agent"` or `"failed_by_agent"`, potentially adding results or error information to the task object.

4.  **Bridge - Task Finalization & Pruning:** `cursor_bridge.py` detects the `"completed_by_agent"` or `"failed_by_agent"` status.
    *   Performs final logging.
    *   Copies relevant files (the task object itself, associated logs from `instructions/`) to the appropriate `instructions/archive/` subdirectory.
    *   Updates the task's status in `task_queue.json` to `"completed"` or `"failed"`.
    *   Removes the task object from the main `tasks` list in `task_queue.json` (pruning).

**Developer Notes for Cursor Bridge:**
*   The clear separation of concerns between `instruction_details` (what the Meta-Agent wants) and `agent_action_details` (how the agent should do it) is a key design principle.
*   Pydantic models in `models.py` are crucial for maintaining data integrity. Any changes to task structures should start there.
*   The stability of `cursor_bridge.py` is the most pressing issue. Debugging should focus on its polling loop, file handling, and interactions with `task_queue.json` to identify the cause of hangs/timeouts.