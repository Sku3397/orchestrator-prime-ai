# Orchestrator Prime

## Overview

Orchestrator Prime is a terminal-based AI orchestration application. It's designed with two primary modes of operation:

1.  **Orchestrator Prime Core:** Manages and automates software development tasks by coordinating between a large language model (like Google's Gemini) acting as a "Dev Manager" and a simulated AI coding agent (like Cursor, interacting via the file system) acting as a "Dev Engineer". The primary goal is to take a high-level project goal, break it down, instruct the agent, and process results iteratively.
2.  **Cursor Bridge Module:** A system designed to allow an external Meta-Agent to delegate specific file modification or command execution tasks to a "Cursor-like" agent. This module processes tasks defined in a `task_queue.json` file. *This is the component that has been the focus of recent testing and is currently experiencing stability issues (hangs/timeouts).*

This README primarily covers the setup and usage of the Orchestrator Prime Core. Details specific to the Cursor Bridge module are in the "Cursor Bridge Module" section below and in `ORCHESTRATOR_PRIME_ARCHITECTURE.md`.

## Features

*   **Terminal Interface:** All interactions happen via text commands in the console.
*   **Project Management:** Add, list, and select development projects to work on.
*   **AI Orchestration:** Uses Gemini (configurable) to generate instructions based on project goals, history, and agent feedback.
*   **File-Based Agent Communication:** Interacts with the "Dev Engineer" (Cursor agent simulation) by writing instructions to `dev_instructions/next_step.txt` and reading results from `dev_logs/cursor_step_output.txt` within the target project's workspace.
*   **State Persistence:** Saves project details and conversation state (`.orchestrator_state/state.json` within project workspace, `app_data/projects.json` globally).
*   **Logging:** Logs detailed operational information to `orchestrator_prime.log`.

## Setup

1.  **Prerequisites:**
    *   Python 3.8+ recommended.
    *   Git (optional, for version control).

2.  **Clone the Repository (if applicable):**
    ```bash
    git clone <repository_url>
    cd orchestrator_prime
    ```

3.  **Create a Virtual Environment:**
    ```bash
    python -m venv venv
    # Activate the environment
    # Windows (Command Prompt/PowerShell):
    .\venv\Scripts\activate
    # macOS/Linux (Bash/Zsh):
    source venv/bin/activate
    ```

4.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

5.  **Configure API Key:**
    *   The application requires a Google AI (Gemini) API key.
    *   A `config.ini` file will be created automatically on first run if it doesn't exist.
    *   Open `config.ini` and replace `YOUR_API_KEY_HERE` with your actual Gemini API key:
        ```ini
        [API]
        gemini_api_key = YOUR_ACTUAL_GEMINI_API_KEY
        gemini_model = gemini-1.5-flash-latest
        ```
    *   You can also configure other settings like the Gemini model to use, timeouts, etc., in `config.ini`.

## Running Orchestrator Prime

Ensure your virtual environment is activated.

```bash
python main.py
```

The application will start, and you will see the `OP > ` prompt.

## Terminal Commands

Type `help` at the prompt to see this list:

*   `project list`
    *   Lists all projects stored in `app_data/projects.json`.
*   `project add`
    *   Starts an interactive prompt to add a new project. You will be asked for:
        *   **Project Name:** A unique name for your project.
        *   **Workspace Root Path:** The **absolute path** to the directory containing the code/files for this project.
        *   **Overall Goal:** The high-level objective for this project.
*   `project select <name>`
    *   Selects the project with the given `<name>` as the active project for subsequent commands.
    *   The prompt will change to `OP (Project: <name>) > `.
*   `goal <initial goal text>`
    *   Starts a new orchestration task for the *currently selected project* using the provided text as the initial instruction for the Dev Manager (Gemini).
*   `(Any other text)`
    *   If a project is selected, any text that is not one of the above commands will be treated as an initial instruction (like the `goal` command).
*   `input <response text>`
    *   Used only when the engine state is `PAUSED_WAITING_USER_INPUT` (i.e., Gemini has asked a question).
    *   Sends the `<response text>` back to Gemini to allow the task to continue.
*   `status`
    *   Displays the current state of the engine, the active project details (name, goal), the current task goal (if any), the last instruction sent to the simulated Cursor agent (if applicable), and any pending questions or error messages.
*   `stop`
    *   Attempts to gracefully stop the currently running task for the active project.
*   `quit`
    *   Shuts down the Orchestrator Prime application and saves the current state.

## Project Workspace Structure

When Orchestrator Prime manages a project located at `<workspace_root_path>`, it expects to interact with the following directories within that path:

*   `<workspace_root_path>/dev_instructions/`: Orchestrator Prime writes the next instruction for the simulated agent into `next_step.txt` in this directory.
*   `<workspace_root_path>/dev_logs/`: The simulated agent is expected to write its results, errors, or clarification requests into `cursor_step_output.txt` in this directory. Processed logs are moved to `dev_logs/processed/`.
*   `<workspace_root_path>/.orchestrator_state/`: Orchestrator Prime saves the specific state (conversation history, etc.) for this project in `state.json` within this hidden directory.

You need to ensure the main `<workspace_root_path>` exists when adding a project. The subdirectories (`dev_instructions`, `dev_logs`, `.orchestrator_state`) will be created automatically by Orchestrator Prime if they don't exist when a project is selected.

## Logging

Detailed operational logs for the Orchestrator Prime Core are written to `orchestrator_prime.log` in the directory where you run `python main.py`. Logs include state changes, API calls, file operations, errors, and warnings.

Logs for the Cursor Bridge module are typically directed to `cursor_bridge.log` and task-specific logs within the `instructions/` directory (e.g., `<task_id>.output.log`).

## Cursor Bridge Module (File-Based Task Delegation)

This system allows an external Meta-Agent to provide specific, structured tasks to `cursor_bridge.py` for execution, primarily focused on file modifications and command execution.

-   **`task_queue.json`**: The central list of tasks. The Meta-Agent adds tasks here (e.g., modify a file, run a command). `cursor_bridge.py` reads from this queue and updates task statuses within this file.
-   **`cursor_bridge.py`**: The script responsible for polling `task_queue.json` for pending tasks. It processes these tasks, delegates actions (like file edits or command executions) to an underlying agent or performs them directly, and logs the results. *Currently, this script is experiencing significant stability issues, often hanging or timing out during execution.*
-   **`rth_local_copy.py`**: A utility script (a copy of `robust_terminal_handler.py`) used to launch `cursor_bridge.py` with added robustness, including timeout management and status reporting. This is the recommended way to run `cursor_bridge.py`.
-   **`instructions/`**: Directory for communication and logging files related to `cursor_bridge.py` operations:
    -   `cursor_bridge.log`: General log for `cursor_bridge.py` operations.
    -   Task-specific logs (e.g., `<task_id>.output.log`): Detailed output for individual tasks.
    -   Error files (e.g., `<task_id>.error.json`): Structured error information if a task fails.
    -   `archive/`: Contains subdirectories for `processed` and `failed` task-related files (e.g., original task JSONs, logs).
-   **`templates/`**: Contains JSON schemas defining the structure for tasks in `task_queue.json`, status objects, and error reports.
-   **`models.py`**: Defines Pydantic models for tasks, instructions, and status objects, ensuring data integrity for `task_queue.json` and inter-component communication within the Cursor Bridge module.

### Intended Workflow (File Modification Example)
1.  A Meta-Agent (or test script) creates a task in `task_queue.json`. For a file modification, this task would specify:
    *   `task_id`
    *   `status: "pending"`
    *   `instruction_details`:
        *   `file_path`: The target file.
        *   `action`: The type of modification (e.g., `replace_content`, `append_content`, `insert_before_line`, `delete_lines`, `replace_lines`).
        *   Action-specific parameters (e.g., `content`, `line_marker`, `start_line_number`, `replacement_content`).
2.  `cursor_bridge.py` (launched via `rth_local_copy.py`) polls `task_queue.json`.
3.  On finding a "pending" task, `cursor_bridge.py` updates its status to `pending_agent_action` (or a similar state indicating it's preparing for agent execution). It populates `agent_action_details` within the task object in `task_queue.json`, specifying the tool the underlying agent should call (e.g., `edit_file`) and the parameters for that tool.
4.  A simulated agent (or in these tests, the test orchestrator like myself) reads `task_queue.json`, finds the task with `pending_agent_action`, and executes the specified action using the details from `agent_action_details`.
5.  The agent then updates the task status in `task_queue.json` to `completed_by_agent` (or `failed_by_agent`).
6.  `cursor_bridge.py` observes this change, finalizes the task (e.g., moves it to an archive, performs final logging), and updates its status to `completed` (or `failed`) in `task_queue.json`. It then prunes the completed/failed task from the main queue.

### Intended Workflow (Command Execution Example)
1.  A Meta-Agent adds a task to `task_queue.json` specifying `commands_to_execute`, including command strings, working directories, and RTH timeout configurations.
2.  `cursor_bridge.py` picks up this task.
3.  `cursor_bridge.py` updates the status to `pending_agent_action` and populates `agent_action_details` instructing an agent to use a tool like `execute_commands_sequentially`. This includes paths to `robust_terminal_handler.py`, the Python executable for RTH, and a base directory for RTH outputs.
4.  An agent executes the command(s) via RTH, captures outputs, and updates the task in `task_queue.json` to `completed_by_agent` or `failed_by_agent`, including results like RTH status, stdout, and stderr.
5.  `cursor_bridge.py` finalizes and prunes the task.

## For Developers

This project combines direct LLM interaction for high-level task orchestration (Orchestrator Prime Core) with a more structured, file-based task processing system (Cursor Bridge Module).

Key files/modules to understand:
*   **`main.py`**: Entry point for the Orchestrator Prime Core terminal application. Handles user input and the main application loop.
*   **`engine.py`**: Contains the core logic for the Orchestrator Prime's LLM interaction, state management, and communication with the simulated agent via `dev_instructions/` and `dev_logs/`.
*   **`cursor_bridge.py`**: The main script for the Cursor Bridge Module. Responsible for polling `task_queue.json`, processing tasks, and (intendedly) delegating actions. **This script is currently unstable.**
*   **`rth_local_copy.py` / `robust_terminal_handler.py`**: Utility for robustly executing command-line processes, crucial for running `cursor_bridge.py` and any commands it might delegate.
*   **`task_queue.json`**: The central communication channel for the Cursor Bridge Module. Its structure is defined by models in `models.py`.
*   **`models.py`**: Defines Pydantic models for tasks, instructions, and status objects, ensuring data integrity for `task_queue.json` and inter-component communication within the Cursor Bridge module.
*   **`config_manager.py`**: Handles reading and writing configuration settings from `config.ini`.
*   **`persistence.py`**: Manages saving and loading state for the Orchestrator Prime Core.

To get a deeper understanding of the system architecture, especially the intended flows and data structures for the Cursor Bridge Module, please refer to `ORCHESTRATOR_PRIME_ARCHITECTURE.md`.

When debugging or extending the Cursor Bridge Module, pay close attention to:
- The structure of `task_queue.json` and how tasks are updated through their lifecycle.
- The interaction between `cursor_bridge.py` and the file system (reading/writing task files, logs, and potentially target files for modification).
- The use of `rth_local_copy.py` for invoking `cursor_bridge.py` and capturing its outcome.
- The Pydantic models in `models.py` that define the expected data structures.

Given the current issues with `cursor_bridge.py`, initial debugging efforts should focus on why it hangs or times out, even with simple tasks in `task_queue.json`. 