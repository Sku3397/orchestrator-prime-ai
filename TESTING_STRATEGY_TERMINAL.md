# Orchestrator Prime: Terminal App Testing Strategy

**Objective:** Ensure the terminal-based Orchestrator Prime application is robust, functional, and handles core workflows correctly.

**Primary Test Script:** `test_terminal_app.py`

**Methodology:**
1.  **Subprocess Execution:** Tests run `python main.py` as a subprocess using Python's `subprocess.Popen`.
2.  **Simulated User Input:** Commands are fed to Orchestrator Prime's `stdin` (e.g., `process.stdin.write(b"project select TestProj1\n"); process.stdin.flush()`).
3.  **Output Capture & Parsing:** Orchestrator Prime's `stdout` and `stderr` are captured. Test assertions are made against expected output patterns (status messages, prompts for input, Gemini responses). Timeouts are used for reading output.
4.  **File System Simulation (for "Dev Engineer"):**
    *   Test scripts create/modify files in a temporary test project's workspace (`./dev_logs/cursor_step_output.txt`, `./dev_instructions/next_step.txt`) to simulate the actions of the "Dev Engineer" (Cursor Agent).
    *   This includes simulating the ~60-second WAIT period from the Dev Engineer SOP before creating `cursor_step_output.txt` to test Orchestrator Prime's file watcher and timeout logic correctly.
5.  **Mocking `gemini_comms.py`:**
    *   A robust mocking infrastructure is in `test_terminal_app.py`.
    *   `MOCK_GEMINI_COMMS_TEMPLATE`: A string template for a mock `gemini_comms.py`.
    *   `apply_gemini_comms_mock(mock_type, details=None)`: Backs up original `gemini_comms.py`, writes the template formatted with specific mock behavior (e.g., return "INSTRUCTION", "NEED_INPUT", "TASK_COMPLETE", "ERROR_API_AUTH").
    *   `restore_gemini_comms_original()`: Restores the live `gemini_comms.py`.
    *   **Usage:** Tests default to LIVE Gemini API. Mocking is used *only* for specific scenarios where a deterministic Gemini response is needed to test a particular Orchestrator Prime state transition or error handling path (e.g., forcing a `NEED_USER_INPUT` or a specific API error). Mocking is clearly logged and reverted.
6.  **State Verification:** Tests check `engine.state` (via `status` command output or by inspecting saved `state.json` if necessary) and content of relevant files (`projects.json`, `state.json`).
7.  **Cleanup:** Test environment (temp project dirs, mock files) should be cleaned up after tests.

**Test Execution Methodology (for Agent performing tests):**
*   Run the full test suite (`python test_terminal_app.py`).
*   If a test fails:
    1.  Analyze the failure (Orchestrator Prime output, test script assertions, relevant file states).
    2.  Diagnose the root cause in Orchestrator Prime's core code (`main.py`, `engine.py`, etc.) or the test script itself.
    3.  Implement a fix.
    4.  **Re-run the *entire* test suite** to ensure no regressions.
    5.  Repeat until all tests pass.
*   The "background terminal and poll output" strategy is for *manual user interaction with the agent during development of Orchestrator Prime*, not for how `test_terminal_app.py` itself runs. The test script directly controls and observes the Orchestrator Prime subprocess.