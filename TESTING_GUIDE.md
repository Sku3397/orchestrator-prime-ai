# Testing Guide for Orchestrator Prime

This document outlines the testing strategy and provides guidance on running and writing tests for the Orchestrator Prime project, covering both the Orchestrator Prime Core and the Cursor Bridge Module.

## Overview

Testing is crucial to ensure the reliability and correctness of the application. The project employs a combination of:
-   End-to-end tests for the Orchestrator Prime Core (simulating user interaction and Gemini responses).
-   Component/integration tests for the Cursor Bridge Module (testing task processing, file modifications, and command executions via `task_queue.json`).

## Orchestrator Prime Core Testing

### Existing Tests
-   **`test_terminal_app.py`**: This is the primary test script for the Orchestrator Prime Core.
    -   It uses Python's `subprocess` module to run `main.py` as a separate process.
    -   It simulates user input being typed into the terminal.
    -   It simulates the "Dev Engineer" (Cursor agent) by creating/modifying `dev_logs/cursor_step_output.txt` in response to instructions found in `dev_instructions/next_step.txt`.
    -   It employs a mocking mechanism for `gemini_comms.py` to provide predefined Gemini API responses, allowing for deterministic testing of the `OrchestrationEngine`'s logic and state transitions.
        -   Mock responses are defined in `MOCK_GEMINI_COMMS_TEMPLATE` within `test_terminal_app.py`.
        -   `apply_gemini_comms_mock()` and `restore_gemini_comms_original()` functions are used to switch between mocked and real Gemini communicators.

### Running Core Tests

(Detailed instructions would go here, e.g.,)
```bash
python test_terminal_app.py
```
-   Ensure that any necessary mock configurations in `test_terminal_app.py` are set up for the specific test scenario.
-   Test outputs and statuses are typically printed to the console by `test_terminal_app.py`.

### Writing New Core Tests
-   New test cases for `test_terminal_app.py` should follow the existing pattern:
    1.  Define a sequence of user inputs.
    2.  Define corresponding mock Gemini responses (if the test involves LLM interaction).
    3.  Define expected agent log file content and when it should be written.
    4.  Assert the final state of the application or specific outputs.
-   Consider edge cases, error conditions, and different user command sequences.

## Cursor Bridge Module Testing

Testing for the Cursor Bridge Module primarily involves creating specific task files in `task_queue.json` and then running `cursor_bridge.py` (via `rth_local_copy.py`) to observe its behavior.

### Current Test Approach (Manual/Scripted Workflow)

The tests performed recently (Campaign 1) followed this pattern:

1.  **Setup Target Files**: Create any files that the task will modify (e.g., `test_append.txt`).
2.  **Prepare `task_queue.json`**: Populate `task_queue.json` with a single task definition, including `instruction_details` for the specific action (e.g., `append_content`, `replace_lines`).
3.  **Run `cursor_bridge.py` for Delegation**: Execute `cursor_bridge.py` using `rth_local_copy.py` with appropriate timeouts and a unique status file path. The expectation is that `cursor_bridge.py` will update the task in `task_queue.json` to `pending_agent_action` and populate `agent_action_details`.
    *   *Current Challenge*: `cursor_bridge.py` has been consistently timing out at this stage.
4.  **Simulate Agent Execution**: Manually (or via script) parse the `agent_action_details` from `task_queue.json` and perform the specified action (e.g., call the `edit_file` API for file modifications).
5.  **Update `task_queue.json` (Agent)**: Manually (or via script) update the task status to `completed_by_agent`.
6.  **Verify File Changes**: Check that the target file has been modified as expected.
7.  **Run `cursor_bridge.py` for Pruning**: Execute `cursor_bridge.py` again. The expectation is that it will process the `completed_by_agent` task, archive relevant files, and remove the task from `task_queue.json`.
    *   *Current Challenge*: `cursor_bridge.py` has also been timing out at this stage.
8.  **Verify Pruning & Archival**: Check that `task_queue.json` is empty (or the task is gone) and that files have been moved to the `instructions/archive/processed/` directory.
9.  **Cleanup**: Delete test files and RTH status files.

### Automating Cursor Bridge Tests

-   A dedicated Python test script could automate the steps above.
-   This script would:
    -   Programmatically create `task_queue.json` with various task types.
    -   Launch `rth_local_copy.py` with `cursor_bridge.py`.
    -   Poll `task_queue.json` and RTH status files to check for expected state changes.
    -   Simulate the agent part (performing edits or command executions based on `agent_action_details`).
    -   Assert final file states, `task_queue.json` states, and archive contents.
-   This would require careful management of subprocesses and file I/O.

### Key Areas to Test for Cursor Bridge:
-   Each file modification action (`replace_content`, `append_content`, `insert_before_line`, `insert_after_line`, `delete_lines`, `replace_lines`).
-   Command execution (`execute_commands_sequentially`) with single and multiple commands.
-   Error handling: What happens if a target file doesn't exist? What if a command fails?
-   Correct population of `agent_action_details` by `cursor_bridge.py`.
-   Correct processing of `completed_by_agent` / `failed_by_agent` statuses by `cursor_bridge.py`.
-   Archival and pruning logic.
-   Robustness with malformed tasks in `task_queue.json`.

## General Testing Principles
-   **Readable Tests**: Tests should be easy to understand.
-   **Independent Tests**: Tests should not depend on the state left by other tests, where possible.
-   **Repeatable Tests**: Tests should produce the same results every time they are run in the same environment.
-   **Focused Tests**: Each test should ideally verify a small piece of functionality.

(Further sections could include: Setting up a test environment, specific tools or libraries used for testing beyond subprocess, guidelines for mocking dependencies for the Cursor Bridge module if it starts having external dependencies beyond file system interaction.) 