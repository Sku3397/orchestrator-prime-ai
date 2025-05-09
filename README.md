# Orchestrator Prime

## Overview

Orchestrator Prime is a terminal-based AI orchestration application designed to manage and automate software development tasks. It coordinates between a large language model (like Google's Gemini) acting as a "Dev Manager" and a simulated AI coding agent (like Cursor, interacting via the file system) acting as a "Dev Engineer".

The primary goal is to take a high-level project goal, break it down into steps via the LLM, instruct the coding agent to perform those steps, process the agent's results (success, errors, clarifications needed), and continue the cycle until the goal is achieved or requires user intervention.

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

Detailed operational logs are written to `orchestrator_prime.log` in the directory where you run `python main.py`. Logs include state changes, API calls, file operations, errors, and warnings. 