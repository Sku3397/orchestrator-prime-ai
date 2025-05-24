# Troubleshooting Orchestrator Prime

This document provides guidance on common issues and how to diagnose them.

## Cursor Bridge Module (`cursor_bridge.py`) Instability

This is the most significant known issue area.

**Symptoms:**
-   `cursor_bridge.py`, when run (typically via `rth_local_copy.py`), times out. This is indicated by `rth_local_copy.py` reporting `RTH_EXEC_TERMINATED_TOTAL_TIMEOUT` in its status file.
-   The RTH status file might be created, but the stdout/stderr temp files listed within it might be empty or non-existent, suggesting `cursor_bridge.py` hung before producing output or RTH cleaned them up.
-   `cursor_bridge.py` fails to process tasks in `task_queue.json` (tasks remain in `pending` or `pending_agent_action` indefinitely, no archival occurs).
-   `cursor_bridge.log` may be empty or may not show activity corresponding to the problematic task processing attempt.

**Diagnostic Steps:**

1.  **Simplify `task_queue.json`**: Reduce `task_queue.json` to a single, very simple task (e.g., a file modification on a non-existent or tiny file, or a very simple command). This helps isolate whether the issue is task-specific or a general problem with `cursor_bridge.py`'s startup/polling logic.

2.  **Check `rth_local_copy.py` Invocation**:
    -   Ensure the command used to invoke `rth_local_copy.py` correctly specifies paths to `python.exe` for RTH, `rth_local_copy.py` itself, and `cursor_bridge.py`.
    -   Verify that the `--status-file-path` is unique for each run and that RTH has permissions to write to that path.
    -   Experiment with timeout values for RTH (`--total-timeout`, `--launch-timeout`, `--activity-timeout`). While `cursor_bridge.py` is hanging, very short timeouts might not reveal much, but excessively long ones will just delay observing the failure.

3.  **Examine RTH Status and Output Files**:
    -   Always check the JSON status file produced by `rth_local_copy.py`. Note the `final_handler_status_message` and `subprocess_exit_code`.
    -   Attempt to read the `stdout_temp_file_used` and `stderr_temp_file_used` specified in the RTH status file. If they don't exist, it indicates RTH might have cleaned them up or `cursor_bridge.py` produced no output.
        ```powershell
        # Example to read RTH stdout temp file (path from RTH status JSON)
        Get-Content -Path "C:\path\to\rth_child_stdout_XXXXXX"
        ```

4.  **Add Debug Logging to `cursor_bridge.py` (Requires Code Modification)**:
    -   If possible, add very early and frequent `print()` statements or file-based logging at the beginning of `cursor_bridge.py` and within its main polling loop and file/task processing sections.
    -   Example: Log when the script starts, when it loads `task_queue.json`, what tasks it finds, which task it attempts to process, before and after key operations (like file I/O, status updates).
    -   Ensure these logs are flushed (`print(..., flush=True)` or `file.flush()`) to prevent buffering from hiding the last messages before a hang.

5.  **Run `cursor_bridge.py` Directly (Simplified Execution)**:
    -   Try running `cursor_bridge.py` directly with `python.exe cursor_bridge.py` without RTH. This removes RTH as a variable but means you lose timeout protection.
    -   Observe console output directly. If it hangs, the last printed message (if any) might give a clue to where it's stuck.
    -   A helper script like `temp_run_cb.py` (used in previous tests) can be useful to run it with a basic Python subprocess timeout:
        ```python
        # temp_run_cb.py (simplified)
        import subprocess
        import sys
        try:
            process = subprocess.Popen([sys.executable, "cursor_bridge.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            stdout, stderr = process.communicate(timeout=60) # 60s timeout
            print("STDOUT:", stdout)
            print("STDERR:", stderr)
        except subprocess.TimeoutExpired:
            print("cursor_bridge.py timed out directly!")
            process.kill()
        ```

6.  **Check File Permissions and Paths**:
    -   Ensure `cursor_bridge.py` has read/write permissions for `task_queue.json`, the `instructions/` directory (and its subdirectories like `archive/`), and any target files specified in tasks.
    -   Verify all paths configured or used by `cursor_bridge.py` (e.g., in `instruction_details` or `agent_action_details`) are correct and accessible.

7.  **Examine `watchdog` Usage (If Confirmed as Used)**:
    -   The `watchdog` library is used by the Orchestrator Prime Core for monitoring `dev_logs/`. If `cursor_bridge.py` also uses `watchdog` (e.g., to monitor `task_queue.json`), misconfiguration (e.g., watching too many files, incorrect handler logic) can lead to hangs or excessive resource usage. This would require inspecting the `cursor_bridge.py` source code.

8.  **Resource Issues**: Unlikely with simple test cases, but ensure the system is not under extreme load (CPU, memory, disk I/O) that could cause processes to become unresponsive.

## Orchestrator Prime Core Issues

(This section can be expanded as issues are identified with the main `main.py`/`engine.py` loop.)

-   **Gemini API Errors**:
    -   **Symptom**: Errors reported in `orchestrator_prime.log` related to API calls, or messages like "Failed to get a valid response from Gemini."
    -   **Diagnostics**:
        -   Check `config.ini` for a correct and valid `gemini_api_key`.
        -   Ensure the machine has internet connectivity.
        -   Look at the specific error message from the Gemini API in the logs for more clues.
-   **File Watcher Issues (Orchestrator Prime Core)**:
    -   **Symptom**: `engine.py` doesn't react when `dev_logs/cursor_step_output.txt` is created/modified by the simulated agent.
    -   **Diagnostics**:
        -   Check `orchestrator_prime.log` for messages related to the file watcher starting or events being detected.
        -   Verify the paths being watched are correct for the active project.
        -   Ensure no other process is exclusively locking `cursor_step_output.txt`.

## General Advice
-   **Logging is Key**: Always consult `orchestrator_prime.log` (for the core) and `cursor_bridge.log` / RTH outputs (for the bridge module) as the first step.
-   **Isolate the Problem**: Try to simplify the scenario that triggers the issue. If it's a complex task, break it down.
-   **Check Dependencies**: Ensure all dependencies in `requirements.txt` are correctly installed in your virtual environment. 