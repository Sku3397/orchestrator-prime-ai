import google.generativeai as genai
from .config_manager import ConfigManager
import os
from typing import Optional, List

# Standard Operating Procedure for Cursor (Dev Engineer)
CURSOR_SOP_PROMPT = """\
# Standard Operating Procedure (SOP) for Dev Engineer (Cursor)

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
        *   **`SUCCESS:`** Followed by a concise summary of what was done, including files changed/created, and any important observations. If the task was to answer a question or provide information, provide it here.
        *   **`ERROR:`** Followed by a detailed description of the error encountered, any error messages, and the steps taken that led to the error. This helps the Dev Manager diagnose the problem.
        *   **`CLARIFICATION_NEEDED:`** Followed by the specific question for the Dev Manager, as mentioned in Step 2.
        *   **`PARTIAL_SUCCESS:`** If a multi-step instruction was only partially completed before encountering an issue or needing a natural break. Describe what was completed and what remains or is problematic.
    *   Be precise and provide enough detail for the Dev Manager to understand the outcome without needing to guess.
    *   Example `cursor_step_output.txt` content:
        ```
        SUCCESS: Created `user_interface.py`. Implemented the `login_screen` function as per specification. Added basic input validation for username and password fields.
        ```
        OR
        ```
        ERROR: Failed to install package 'example-lib'. Pip command returned non-zero exit code. Error message: 'Could not find a version that satisfies the requirement example-lib'.
        ```

5.  **WAIT for Next Instruction:**
    *   After writing to `cursor_step_output.txt`, **DO NOTHING ELSE related to the Orchestrator Prime task.**
    *   Wait for `dev_instructions/next_step.txt` to be updated by Orchestrator Prime with the next instruction or clarification.
    *   Do not assume the next step. Do not proactively start new tasks not explicitly instructed.

**Self-Correction/Problem Solving:**

*   If you encounter a minor, easily solvable issue (e.g., a typo in a variable name you just wrote, a simple import error you can fix), attempt to fix it and proceed.
*   If a task is more complex than initially anticipated or if you face a significant blocker, document this in the log file (`ERROR:` or `CLARIFICATION_NEEDED:`).
*   Do not go down long rabbit holes of debugging or research unless the instruction explicitly asks for it. Prioritize informing the Dev Manager.

**File Naming and Locations:**

*   **Instructions from Dev Manager:** `{project_workspace}/dev_instructions/next_step.txt`
*   **Your Output Log:** `{project_workspace}/dev_logs/cursor_step_output.txt`

**Key Reminders:**

*   **Communication is key:** The `cursor_step_output.txt` file is your ONLY way to communicate back.
*   **Follow instructions precisely.**
*   **Wait for your turn.** Do not act out of sequence.
"""

class GeminiCommunicator:
    def __init__(self):
        try:
            # Assuming config_manager.py is in the same directory or accessible in PYTHONPATH
            # Adjust path if orchestrator_prime is not the root for execution context
            current_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(current_dir, 'config.ini') 
            if not os.path.exists(config_path) and os.path.basename(current_dir) == "orchestrator_prime":
                 # Try one level up if running from within orchestrator_prime dir and config is at root of it
                 config_path = os.path.join(os.path.dirname(current_dir), 'orchestrator_prime', 'config.ini')
            
            # A more robust way if we know the project root or how main.py is run:
            # For now, let's assume config.ini is discoverable by ConfigManager's default.
            self.config_manager = ConfigManager() 
            api_key = self.config_manager.get_api_key()
            if not api_key or api_key == "YOUR_API_KEY_HERE":
                raise ValueError("API key not configured in config.ini or is placeholder.")
            genai.configure(api_key=api_key)
            # TODO: Allow model selection from config
            self.model = genai.GenerativeModel('gemini-1.5-flash-latest') # Or your preferred model
        except FileNotFoundError as e:
            print(f"ERROR (GeminiComms): Configuration file not found. {e}")
            raise
        except ValueError as e:
            print(f"ERROR (GeminiComms): Configuration error. {e}")
            raise
        except Exception as e:
            print(f"ERROR (GeminiComms): Failed to initialize Gemini client: {e}")
            raise

    def get_next_step_from_gemini(self, project_goal: str, 
                                  context_summary: Optional[str], 
                                  recent_history: List[dict], 
                                  cursor_log_content: Optional[str]) -> tuple[str, str]:
        """ 
        Communicates with Gemini to get the next step.
        Returns a tuple: (status_code, content)
        status_code can be: INSTRUCTION, NEED_INPUT, TASK_COMPLETE, ERROR
        """
        # TODO: Implement actual context summarization logic
        # For now, context_summary is a placeholder.
        _ = context_summary # Mark as used

        # Construct the prompt
        prompt_parts = [
            "You are Dev Manager, an AI assistant orchestrating a development task with a Dev Engineer (a large language model like Cursor).",
            f"The overall project goal is: {project_goal}",
        ]

        if recent_history:
            prompt_parts.append("\nRecent conversation history (User is the human, you are Dev Manager, Dev Engineer is Cursor via log files):")
            for entry in recent_history[-10:]: # Last 10 turns
                if entry.get('sender') == 'user':
                    prompt_parts.append(f"User: {entry['message']}")
                elif entry.get('sender') == 'gemini': # This is you, the Dev Manager
                    prompt_parts.append(f"You (Dev Manager): {entry['message']}")
                elif entry.get('sender') == 'status': # Internal status messages
                    prompt_parts.append(f"System Status: {entry['message']}")
                # Dev Engineer (Cursor) input is via cursor_log_content

        if cursor_log_content:
            prompt_parts.append(f"\nThe Dev Engineer (Cursor) has provided the following log from its last step (`cursor_step_output.txt`):
---
{cursor_log_content}
---")
        else:
            prompt_parts.append("\nThis is the first instruction or the Dev Engineer has not yet provided a log.")

        prompt_parts.extend([
            "\nBased on the goal, history, and the Dev Engineer's latest log (if any), provide the *next single, actionable instruction* for the Dev Engineer.",
            "If the Dev Engineer indicated `CLARIFICATION_NEEDED: [Question]`, answer their question and provide the next instruction.",
            "If the Dev Engineer indicated `ERROR:`, analyze the error and provide guidance or a corrected instruction.",
            "If you need input from the human user before proceeding, respond ONLY with `NEED_USER_INPUT: [Your question for the user]`.",
            "If the overall project goal appears to be complete based on the Dev Engineer's logs and the history, respond ONLY with `TASK_COMPLETE`.",
            "Instructions should be clear, concise, and broken down into manageable steps if complex. Assume the Dev Engineer has access to the project workspace and can read/write files and run terminal commands.",
            "Focus on one discrete step at a time for the Dev Engineer.",
            "Do NOT ask the Dev Engineer to monitor files or wait for you; the orchestrator handles that. Just give the next instruction."
        ])
        
        full_prompt = "\n".join(prompt_parts)
        # print(f"\n--- PROMPT TO GEMINI ---\n{full_prompt}\n------------------------\n") # For debugging

        try:
            response = self.model.generate_content(full_prompt)
            response_text = response.text.strip()
            # print(f"\n--- RESPONSE FROM GEMINI ---\n{response_text}\n----------------------------\n") # For debugging

            if response_text.startswith("NEED_USER_INPUT:"):
                return "NEED_INPUT", response_text.replace("NEED_USER_INPUT:", "", 1).strip()
            elif response_text == "TASK_COMPLETE":
                return "COMPLETE", "Project goal achieved."
            elif not response_text:
                 return "ERROR", "Gemini returned an empty response."
            else:
                # Assume anything else is an instruction
                return "INSTRUCTION", response_text
            
        except Exception as e:
            print(f"Error calling Gemini API: {e}")
            # Check for specific Google API errors if the library provides them
            # For example, if hasattr(e, 'message') or specific error types
            # Safety reasons can be complex to parse, often in response.prompt_feedback
            # For now, a general error message
            if hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
                 return "ERROR", f"Gemini API request blocked. Reason: {response.prompt_feedback.block_reason}. Details: {response.prompt_feedback.safety_ratings}"
            return "ERROR", f"Failed to get response from Gemini: {str(e)}"

# Example Usage (optional)
if __name__ == '__main__':
    try:
        communicator = GeminiCommunicator()
        print("Gemini Communicator initialized.")
        # Simulate a scenario
        goal = "Create a simple Python script to print Hello World."
        history = []
        log = None # First call

        status, content = communicator.get_next_step_from_gemini(goal, None, history, log)
        print(f"Status: {status}, Content: {content}")

        if status == "INSTRUCTION":
            history.append({"sender": "gemini", "message": content})
            # Simulate Cursor completing the task
            cursor_log_output = "SUCCESS: Created hello.py. Script prints 'Hello World'."
            status2, content2 = communicator.get_next_step_from_gemini(goal, None, history, cursor_log_output)
            print(f"Status 2: {status2}, Content 2: {content2}")
            
            if status2 == "INSTRUCTION": # Gemini might ask to test it or something
                 history.append({"sender": "gemini", "message": content2})
                 cursor_log_output_2 = "SUCCESS: Tested hello.py. It prints 'Hello World' correctly."
                 status3, content3 = communicator.get_next_step_from_gemini(goal, None, history, cursor_log_output_2)
                 print(f"Status 3: {status3}, Content 3: {content3}") # Should be TASK_COMPLETE or similar

    except Exception as e:
        print(f"Error in example usage: {e}") 