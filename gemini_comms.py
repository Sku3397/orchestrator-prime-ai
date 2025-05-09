import google.generativeai as genai
import google.api_core.exceptions # For specific API errors
import google.generativeai.types # Added for exception handling
from config_manager import ConfigManager
import os
from typing import Optional, List, Dict, Any
from models import Turn
import time
from dataclasses import dataclass # Assuming Turn is defined elsewhere or add here
from datetime import datetime
import logging # Added

# Get the logger instance
logger = logging.getLogger("orchestrator_prime")

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

GEMINI_MARKER_NEED_INPUT = "NEED_USER_INPUT:"
GEMINI_MARKER_TASK_COMPLETE = "TASK_COMPLETE"
GEMINI_MARKER_SYSTEM_ERROR = "SYSTEM_ERROR:"

# --- MOCKING FOR TESTING ---
MOCK_GEMINI_ENABLED = False 
mock_main_call_count = 0
mock_summary_call_count = 0
# --- END MOCKING ---

# Add Turn dataclass definition if not imported from models.py
# from models import Turn # Uncomment if models.py exists and is correct
@dataclass
class Turn: # Temporary definition if models.py import fails
    sender: str
    message: str
    timestamp: str = ""
    metadata: Optional[Dict[str, Any]] = None

class GeminiCommunicator:
    def __init__(self):
        logger.info("GeminiCommunicator initializing...")
        self.model = None
        self.model_name = "Unknown"
        self.config = ConfigManager()
        api_key = self.config.get_api_key()
        self.model_name = self.config.get_gemini_model()
        # self._test5_first_call_done = False # RESTORED - REMOVED FOR TEST 5

        if MOCK_GEMINI_ENABLED:
            logger.info("GeminiCommunicator initialized in MOCK mode.")
            return

        if not api_key or api_key == 'YOUR_API_KEY_HERE':
            logger.error("API Key not configured in config.ini. Gemini live mode will be disabled.")
            # This is a critical configuration error, but we allow the app to start to report it.
            return # model remains None

        try:
            genai.configure(api_key=api_key)
            logger.info("Checking Available Gemini Models (supporting generateContent)...")
            models_found = False
            available_models_for_log = []
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    available_models_for_log.append(m.name)
                    models_found = True
            if available_models_for_log:
                logger.debug(f"Available models supporting 'generateContent': {available_models_for_log}")
            
            if not models_found:
                logger.warning("No models found supporting 'generateContent' with this API key/setup.")
            else:
                logger.info(f"Attempting to load configured model: {self.model_name}")
                self.model = genai.GenerativeModel(self.model_name)
                # Test call to verify model (e.g., count_tokens on a short string)
                try:
                    self.model.count_tokens("test") # Simple test call
                    logger.info(f"Successfully configured Gemini and loaded model: {self.model_name}")
                except Exception as model_test_e:
                    logger.error(f"Failed to verify loaded model '{self.model_name}' with a test call: {model_test_e}", exc_info=True)
                    self.model = None # Invalidate model if test fails

        except Exception as e:
            logger.critical(f"Failed to configure Gemini or load model '{self.model_name}'. Error: {type(e).__name__} - {e}", exc_info=True)
            self.model = None

    def _estimate_tokens(self, text: str) -> int:
        # This is a very rough heuristic. Actual tokenization depends on the model.
        # Google models usually count 1 token ~ 4 chars for English.
        # Consider using model.count_tokens if available and reliable for estimates.
        estimated = len(text) // 4 
        logger.debug(f"Estimated tokens for text length {len(text)}: {estimated}")
        return estimated

    def summarize_text(self, text_to_summarize: str, max_summary_tokens: int = 1000) -> Optional[str]:
        if MOCK_GEMINI_ENABLED:
            logger.info(f"MOCK GEMINI (Summarizer): Summarizing text of length {len(text_to_summarize)}.")
            time.sleep(0.1) 
            return f"[Mock Summary of input. Original length: {len(text_to_summarize)} chars. Max tokens: {max_summary_tokens}]"

        if not self.model:
            logger.error("Gemini Summarizer: Model not initialized. Cannot summarize.")
            return None

        summarization_prompt = f"""\
Summarize the following conversation/log concisely, focusing on key decisions, completed tasks, and outstanding issues.
Aim for brevity while retaining critical information. The summary should be suitable for providing context to an AI assistant for subsequent tasks.
Do not add any conversational pleasantries or introductory/concluding remarks, only the summary itself.

Text to Summarize:
---
{text_to_summarize}
---
Summary:"""
        
        try:
            generation_config = genai.types.GenerationConfig(
                max_output_tokens=max_summary_tokens, 
                temperature=0.4 
            )
            print(f"GeminiComms: Calling live Gemini API for summarization (model: {self.model_name}).")
            response = self.model.generate_content(summarization_prompt, generation_config=generation_config)
            
            if response.parts:
                summary = response.text.strip()
                print(f"GeminiComms: Successfully summarized text (live). Summary tokens (est): {self._estimate_tokens(summary)}")
                return summary
            elif response.prompt_feedback and response.prompt_feedback.block_reason:
                reason = response.prompt_feedback.block_reason.name if hasattr(response.prompt_feedback.block_reason, 'name') else str(response.prompt_feedback.block_reason)
                print(f"ERROR (GeminiComms Summarizer): Live API request blocked. Reason: {reason}")
                return None
            else:
                print("ERROR (GeminiComms Summarizer): Live API returned an empty response for summarization.")
                return None
        except Exception as e:
            print(f"ERROR (GeminiComms Summarizer): Exception during live summarization API call: {type(e).__name__} - {e}")
            return None

    def get_next_step_from_gemini(self, 
                                  project_goal: str,
                                  full_conversation_history: List[Turn],
                                  current_context_summary: str,
                                  max_history_turns: int,
                                  max_context_tokens: int, 
                                  cursor_log_content: Optional[str],
                                  initial_project_structure_overview: Optional[str] = None
                                  ) -> Dict[str, Any]:
        # Construct the prompt
        prompt_parts = []
        prompt_parts.append(f"Overall Project Goal: {project_goal}\n")

        if initial_project_structure_overview:
            prompt_parts.append(f"--- Initial Project Structure Overview ---\n{initial_project_structure_overview}\n---\n")

        if current_context_summary:
            prompt_parts.append(f"--- Previously Summarized Context ---\n{current_context_summary}\n---\n")
        else:
            prompt_parts.append("--- No Previously Summarized Context ---\n")

        prompt_parts.append("--- Recent Conversation History ---")
        if full_conversation_history:
            for turn in full_conversation_history[-max_history_turns:]:
                # Ensure timestamp is a string. If it's a datetime object, format it.
                ts = turn.timestamp
                if isinstance(ts, datetime):
                    ts = ts.strftime("%Y-%m-%d %H:%M:%S")
                prompt_parts.append(f"[{turn.sender} @ {ts}]: {turn.message}")
        else:
            prompt_parts.append("No recent conversation history available.")
        prompt_parts.append("---\n")

        if cursor_log_content:
            prompt_parts.append(f"--- Dev Engineer's Latest Log (from cursor_step_output.txt) ---\n{cursor_log_content}\n---\n")
        else:
            prompt_parts.append("--- No New Log from Dev Engineer ---\n")
        
        # Explicitly tell Gemini to use the overview if present
        prompt_parts.append("--- Your Instructions to Dev Engineer ---")
        prompt_parts.append("Based on the overall goal, the *provided initial project structure overview (if present)*, summarized context, recent history, and the Dev Engineer's latest log (if any), provide the *next single, actionable instruction* for the Dev Engineer.")
        prompt_parts.append("If the Dev Engineer indicated `CLARIFICATION_NEEDED:`, answer their question and provide the next instruction.")
        prompt_parts.append("If the Dev Engineer indicated `ERROR:`, analyze the error and provide guidance or a corrected instruction.")
        prompt_parts.append("If you need input from the human user before proceeding, respond ONLY with `NEED_USER_INPUT: [Your question for the user]`.")
        prompt_parts.append("If the overall project goal appears to be complete based on the Dev Engineer's logs and the history, respond ONLY with `TASK_COMPLETE`.")
        prompt_parts.append("If you encounter an internal problem or cannot meaningfully proceed, respond ONLY with `SYSTEM_ERROR: [Brief error description]`.")
        prompt_parts.append("Focus on one discrete step at a time for the Dev Engineer. Adhere to their SOP.\n")
        prompt_parts.append("Reference: The Dev Engineer SOP is:")
        prompt_parts.append(CURSOR_SOP_PROMPT) # SOP_PROMPT_TEXT should be a class/instance var

        full_prompt = "\n".join(prompt_parts)

        # Estimate token count (very rough, actual tokenization is complex)
        estimated_tokens = len(full_prompt.split()) # Simple space-based token estimation
        
        # Truncated logging of the prompt
        if len(full_prompt) > 300:
            print(f"--- FINAL PROMPT TO LIVE GEMINI ({self.model_name}) Est. Tokens: {estimated_tokens} ---\n{full_prompt[:200]}...[TRUNCATED]...{full_prompt[-100:]}\n-------------------------\n")
        else:
            print(f"--- FINAL PROMPT TO LIVE GEMINI ({self.model_name}) Est. Tokens: {estimated_tokens} ---\n{full_prompt}\n-------------------------\n")

        try:
            generation_config = genai.types.GenerationConfig(
                max_output_tokens=max_context_tokens, 
                temperature=0.4 
            )
            print(f"GeminiComms: Calling live Gemini API (model: {self.model_name}). Est. Prompt Tokens: {self._estimate_tokens(full_prompt)}")
            response = self.model.generate_content(full_prompt, generation_config=generation_config)
            
            if not response.parts:
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    reason = response.prompt_feedback.block_reason.name if hasattr(response.prompt_feedback.block_reason, 'name') else str(response.prompt_feedback.block_reason)
                    ratings = ", ".join([f"{r.category.name}: {r.probability.name}" for r in response.prompt_feedback.safety_ratings])
                    error_message = f"Live API request blocked. Reason: {reason}. Safety: [{ratings}]"
                    print(f"ERROR (GeminiComms): {error_message}")
                    return {"status": "ERROR", "content": error_message}
                else: 
                    error_message = "Live API returned empty response (no parts, no block reason)."
                    print(f"ERROR (GeminiComms): {error_message}")
                    return {"status": "ERROR", "content": error_message}

            response_text = response.text.strip()
            print(f"GeminiComms: Received response from live API. Length: {len(response_text)}")

            if response_text.startswith(GEMINI_MARKER_NEED_INPUT):
                return {"status": "NEED_INPUT", "content": response_text.replace(GEMINI_MARKER_NEED_INPUT, "", 1).strip()}
            elif response_text == GEMINI_MARKER_TASK_COMPLETE:
                return {"status": "COMPLETE", "content": "Project goal achieved."}
            elif response_text.startswith(GEMINI_MARKER_SYSTEM_ERROR):
                return {"status": "ERROR", "content": f"Gemini system error: {response_text.replace(GEMINI_MARKER_SYSTEM_ERROR, '', 1).strip()}"}
            elif not response_text: 
                 return {"status": "ERROR", "content": "Live API returned an empty response string."}
            else:
                return {"status": "INSTRUCTION", "content": response_text}
            
        except genai.types.generation_types.BlockedPromptException as bpe: 
            print(f"ERROR (GeminiComms): Live API request blocked (BlockedPromptException): {bpe}")
            return {"status": "ERROR", "content": f"Content policy violation: Prompt blocked by Gemini. Details: {bpe}"}
        except genai.types.generation_types.StopCandidateException as sce:
            print(f"ERROR (GeminiComms): Live API response stopped unexpectedly: {sce}")
            return {"status": "ERROR", "content": f"Gemini response stopped prematurely. Content: {sce.last_response.text if sce.last_response else 'N/A'}"}
        except google.api_core.exceptions.GoogleAPIError as gae: # Catch broader API errors
            error_type_name = type(gae).__name__
            error_message = str(gae)
            print(f"ERROR (GeminiComms): Google API Error ({error_type_name}): {error_message}")
            content_to_return = f"Google API Error ({error_type_name}): {error_message}"
            # Check for common specific cases within GoogleAPIError
            if isinstance(gae, google.api_core.exceptions.NotFound):
                 content_to_return = f"Model or resource not found: {error_message}. Check model name in config."
            elif isinstance(gae, google.api_core.exceptions.PermissionDenied) or "API key not valid" in error_message:
                 return {"status": "ERROR_API_AUTH", "content": f"Gemini API Key / Permission Issue: {error_message}. Check Settings & API console."}
            elif isinstance(gae, google.api_core.exceptions.ResourceExhausted): # Rate limiting
                 content_to_return = f"API Rate Limit Exceeded: {error_message}. Please wait and try again."
            
            return {"status": "ERROR", "content": content_to_return}
        except Exception as e: # Catch any other unexpected exceptions
            error_type_name = type(e).__name__
            error_message = str(e)
            print(f"ERROR (GeminiComms): Unexpected Exception during live Gemini API call ({error_type_name}): {error_message}")
            return {"status": "ERROR", "content": f"Unexpected error during Gemini call ({error_type_name}): {error_message}"}

# Example Usage (ensure mock counts are reset if running standalone)
if __name__ == '__main__':
    mock_main_call_count = 0
    mock_summary_call_count = 0
    try:
        print("Running GeminiCommunicator example (Phase 3)...")
        communicator = GeminiCommunicator()
        print("Gemini Communicator initialized.")
        
        test_goal = "Develop a complex application with multiple modules."
        # history_for_test: List[Turn] = [Turn(sender="USER", message="Initial thoughts on module A.")]
        history_for_test: List[Turn] = []

        # Test summarization
        long_text_for_summary = "Turn 1: User said A. Turn 2: Gemini instructed B. Turn 3: Cursor logged C. " * 10
        summary = communicator.summarize_text(long_text_for_summary)
        print(f"Test Summary: {summary}")

        print(f"\n--- Test Main Call 1: Initial instruction ---")
        response1 = communicator.get_next_step_from_gemini(
            project_goal=test_goal,
            full_conversation_history=history_for_test,
            current_context_summary=summary if summary else "",
            max_history_turns=3, # Small for testing
            max_context_tokens=30000, # Large for mock
            cursor_log_content=None,
            initial_project_structure_overview=None
        )
        print(f"Response 1: {response1}")
        if response1["status"] == "INSTRUCTION":
            history_for_test.append(Turn(sender="GEMINI_MANAGER", message=response1["content"]))
        
        print(f"\n--- Test Main Call 2: Cursor provides log ---")
        test_log_content = "SUCCESS: Implemented module A feature 1."
        history_for_test.append(Turn(sender="CURSOR_LOG_SUMMARY", message=test_log_content))
        response2 = communicator.get_next_step_from_gemini(
            project_goal=test_goal,
            full_conversation_history=history_for_test,
            current_context_summary=summary if summary else "",
            max_history_turns=3,
            max_context_tokens=30000,
            cursor_log_content=test_log_content,
            initial_project_structure_overview=None
        )
        print(f"Response 2: {response2}")
        if response2["status"] == "NEED_INPUT": # Mock should return this
            history_for_test.append(Turn(sender="GEMINI_MANAGER", message=response2["content"])) # The question

        print(f"\n--- Test Main Call 3: User responds to NEED_INPUT ---")
        user_response_text = "Proceed with module B."
        history_for_test.append(Turn(sender="USER", message=user_response_text))
        response3 = communicator.get_next_step_from_gemini(
            project_goal=test_goal,
            full_conversation_history=history_for_test,
            current_context_summary=summary if summary else "",
            max_history_turns=3,
            max_context_tokens=30000,
            cursor_log_content=None, # No new cursor log here
            initial_project_structure_overview=None
        )
        print(f"Response 3: {response3}")


    except Exception as e:
        print(f"Error in example usage: {e}")
        import traceback
        traceback.print_exc() 