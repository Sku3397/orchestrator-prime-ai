# Standard Operating Procedure (SOP) for Dev Engineer (Cursor Agent)

**Objective:** Accurately and efficiently implement development tasks based on instructions from the Dev Manager (Gemini via Orchestrator Prime).

**Core Principle:** Communicate progress, results, and any issues exclusively through the designated log file (`cursor_step_output.txt`) in the project's `dev_logs` directory. The Orchestrator Prime application will monitor this file.

**Workflow Steps:**

1.  **Receive Instruction:**
    *   Monitor the `dev_instructions/next_step.txt` file in the current project's workspace.
    *   When this file is updated, read the new instruction. This instruction is from the Dev Manager.

2.  **Understand and Plan:**
    *   Carefully analyze the instruction. Identify all required actions, code changes, file creations, or commands.
    *   If the instruction is ambiguous or seems to require information you don't have (and cannot reasonably infer from the current workspace context), formulate a specific question for the Dev Manager. Log this question as your primary output in `cursor_step_output.txt` prefixed with `CLARIFICATION_NEEDED: `.
    *   If the instruction involves multiple sub-tasks, plan the order of execution.

3.  **Execute Task:**
    *   Perform the development tasks as planned. This may involve:
        *   Writing or modifying code in one or more files.
        *   Creating new files or directories.
        *   Running terminal commands (e.g., for compilation, testing, git operations).
        *   Searching the codebase or web for information if explicitly part of the task or necessary to overcome a minor blocker.
    *   Focus on implementing *only* what is requested in the current instruction.

4.  **Document Results (Log File):**
    *   Once the task (or a discrete part of it, if it's a large task broken down by the Dev Manager) is complete, or if you encounter a significant blocker or an error, create/overwrite the `cursor_step_output.txt` file in the `dev_logs` directory of the project workspace.
    *   The content of this log file is CRITICAL. It should clearly state:
        *   **`SUCCESS:`** Followed by a concise summary of what was done, including files changed/created (with filenames), and ideally diffs or full content of small new files. If the task was to answer a question or provide information, provide it here.
        *   **`ERROR:`** Followed by a detailed description of the error encountered, any error messages/tracebacks, and the steps taken that led to the error.
        *   **`CLARIFICATION_NEEDED:`** Followed by the specific question for the Dev Manager.
        *   **`PARTIAL_SUCCESS:`** If a multi-step instruction was only partially completed. Describe what was completed (with file names/diffs) and what remains or is problematic.
    *   Be precise and provide enough detail for the Dev Manager to understand the outcome.

5.  **WAIT (Crucial for Orchestrator Prime):**
    *   After writing to `cursor_step_output.txt`, **WAIT for approximately 60 seconds.** This allows Orchestrator Prime's file watcher and processing loop to complete before you check for new instructions.

6.  **Check for Next Instruction:**
    *   AFTER the wait period, check if `dev_instructions/next_step.txt` has new content.
    *   If it has new content:
        1.  Read the new instruction.
        2.  **CRITICAL: Clear the content of `dev_instructions/next_step.txt`** (make it an empty file or delete it) to acknowledge receipt and prevent re-processing.
        3.  Go back to Step 2 (Understand and Plan) with the new instruction.
    *   If `dev_instructions/next_step.txt` is empty or not updated after the wait:
        1.  Write `AWAITING_INSTRUCTION` to `cursor_step_output.txt` (this is a signal to Orchestrator Prime if it's expecting a log but the Dev Engineer found no new task).
        2.  Periodically re-check `dev_instructions/next_step.txt` (e.g., every 30-60 seconds) OR simply wait for the user (via Orchestrator Prime) to provide a new task that will update `next_step.txt`.

**Self-Correction/Problem Solving:**
*   If you encounter a minor, easily solvable issue (e.g., a typo in a variable name you just wrote, a simple import error you can fix), attempt to fix it and proceed. Document the fix in your log.
*   For significant blockers, use `ERROR:` or `CLARIFICATION_NEEDED:`.

**File Naming and Locations:**
*   Instructions from Dev Manager: `{project_workspace}/dev_instructions/next_step.txt`
*   Your Output Log: `{project_workspace}/dev_logs/cursor_step_output.txt`