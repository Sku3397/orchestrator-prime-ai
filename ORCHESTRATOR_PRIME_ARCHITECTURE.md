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