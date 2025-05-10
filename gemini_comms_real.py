import google.generativeai as genai
import google.api_core.exceptions
import logging
import os
from typing import Optional, Dict, Any, List

from config_manager import ConfigManager
from models import Turn # Assuming Turn model is defined appropriately

logger = logging.getLogger(__name__)

# Markers for parsing Gemini's special responses
GEMINI_MARKER_NEED_INPUT = "NEED_USER_INPUT:"
GEMINI_MARKER_TASK_COMPLETE = "TASK_COMPLETE"
GEMINI_MARKER_SYSTEM_ERROR = "SYSTEM_ERROR:" # For Gemini reporting an error on its side

# Standard Operating Procedure prompt to guide Gemini
CURSOR_SOP_PROMPT_TEXT = """
# Orchestrator Prime - Gemini SOP

You are an AI assistant integrated into "Orchestrator Prime," a system that helps users accomplish complex, multi-step tasks using a CLI-like interface with a tool similar to "Cursor" (a hypothetical AI-powered code editor/tool executor).

Your primary role is to break down the user's overall goal into a sequence of precise, actionable instructions for the Cursor tool.

**Workflow:**
1.  **Receive Goal & History:** You'll get the user's overall project goal, the conversation history (user, your previous instructions, Cursor's outputs), and optionally a summary of past interactions.
2.  **Determine Next Step:** Based on this, decide the single most logical next instruction for the Cursor tool to execute.
3.  **Formulate Instruction:**
    *   Instructions must be EXPLICIT and SELF-CONTAINED. Do not assume Cursor remembers context from previous instructions you gave it.
    *   If providing code, provide the complete, runnable code block.
    *   If asking Cursor to use a tool, specify all necessary parameters.
    *   Your instruction will be written to a file (e.g., `next_step.txt`) that Cursor reads.
4.  **Output Format (Choose ONE per response):**

    *   **To give an instruction to Cursor:**
        Simply output the instruction text directly. This is the MOST COMMON response.
        Example: `Create a new Python file named 'utils.py' and add a function 'add(a, b)' that returns their sum.`

    *   **If you need more information from the USER:**
        Start your response with the exact marker `NEED_USER_INPUT:` followed by a clear, concise question for the user.
        Example: `NEED_USER_INPUT: What version of Python should the 'utils.py' script target?`

    *   **If the user's overall goal is complete:**
        Start your response with the exact marker `TASK_COMPLETE` followed by a brief confirmation message.
        Example: `TASK_COMPLETE The 'utils.py' script has been created and tested as per the requirements.`

    *   **If you encounter an unrecoverable internal error or cannot proceed:**
        Start your response with the exact marker `SYSTEM_ERROR:` followed by a brief description of the error. This is for *your* internal errors, not errors from Cursor.
        Example: `SYSTEM_ERROR: I am unable to generate a valid instruction due to conflicting information in the history.`

**Key Considerations:**
*   **Be Methodical:** Break down complex goals into smaller, logical steps.
*   **Context is Key:** Pay close attention to the conversation history, especially Cursor's previous outputs (successes, errors, file contents).
*   **Error Handling by Cursor:** Assume Cursor will report back if it fails to execute your instruction. Your next step should then be to analyze Cursor's error output and decide whether to retry, modify the instruction, or ask the user.
*   **Idempotency (where possible):** If an instruction might be retried, design it so re-execution is safe.
*   **Brevity with Clarity:** Instructions should be concise but unambiguous.
*   **Focus:** Output ONLY the instruction, or ONE of the special marker lines. Do not add conversational fluff unless it's part of a `NEED_USER_INPUT` question.
*   **Workspace Context:** You may be provided with an initial overview of the project's file structure. Use this to inform your instructions.
*   **Summaries:** A `current_context_summary` may be provided. This is a summary of older parts of the conversation. Prioritize recent `full_conversation_history` but use the summary for broader context.
*   **Cursor Log Content:** When `cursor_log_content` is provided, it's the output from the *Cursor tool* executing your *previous* instruction. Analyze it carefully to determine the next step.
"""

class GeminiCommunicator:
    def __init__(self):
        logger.info("GeminiCommunicator initializing...")
        self.config = ConfigManager()
        self.model = None
        self.model_name = "" # Initialize before try block

        try:
            api_key = self.config.get_api_key()
            self.model_name = self.config.get_gemini_model()

            if not api_key:
                logger.error("API Key not found in config.ini. Gemini live mode will not function.")
                return
            if "YOUR_API_KEY" in api_key: # Catches "YOUR_API_KEY_HERE" and similar placeholders
                logger.warning(f"API Key is a placeholder ('{api_key}') in config.ini. Gemini live mode will not function. Mocking should be used for tests requiring Gemini interaction.")
                return # self.model remains None

            logger.info(f"Gemini configured with API key (type: {'placeholder' if 'YOUR_API_KEY' in api_key else 'provided'}). Attempting to load model: {self.model_name}")
            
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(self.model_name)
            logger.info(f"genai.GenerativeModel('{self.model_name}') created instance: {type(self.model)}")
            
            # Test with a very small generation to check if model is truly live (optional)
            # try:
            #     self.model.generate_content("test", generation_config=genai.types.GenerationConfig(max_output_tokens=5))
            #     logger.info(f"Successfully made a test call to Gemini model '{self.model_name}'.")
            # except Exception as e_test:
            #     logger.error(f"Test call to Gemini model '{self.model_name}' failed. It might not be truly live or API key is invalid: {e_test}", exc_info=True)
            #     self.model = None # Treat as non-functional if test fails

            if self.model:
                 logger.info(f"Gemini model '{self.model_name}' loaded and seems operational.")
            else:
                 logger.warning(f"Gemini model '{self.model_name}' could not be initialized or test call failed. Live calls will not work.")

        except Exception as e:
            logger.error(f"Error during GeminiCommunicator initialization: {e}", exc_info=True)
            self.model = None # Ensure model is None on any error

    def _construct_prompt(self,
                         project_goal: str,
                         full_conversation_history: List[Turn],
                         current_context_summary: Optional[str],
                         max_history_turns: int,
                         initial_project_structure_overview: Optional[str] = None,
                         cursor_log_content: Optional[str] = None
                         ) -> str:
        
        prompt_parts = [CURSOR_SOP_PROMPT_TEXT]
        prompt_parts.append(f"User's Overall Project Goal: {project_goal}")

        if initial_project_structure_overview:
            prompt_parts.append(f"\n--- Initial Project Structure Overview ---\n{initial_project_structure_overview}")

        if current_context_summary:
            prompt_parts.append(f"\n--- Summary of Earlier Conversation ---\n{current_context_summary}")

        prompt_parts.append("\n--- Recent Conversation History (Oldest to Newest) ---")
        
        # Manage history length
        start_index = max(0, len(full_conversation_history) - max_history_turns)
        for turn in full_conversation_history[start_index:]:
            sender_map = {
                "user": "User",
                "assistant": "Your Previous Instruction/Response",
                "GEMINI_MANAGER": "Your Previous Instruction/Response", # Treat as assistant's turn
                "cursor_log": "Cursor Tool Output",
                "system": "System Message"
            }
            # Basic turn formatting
            turn_text = f"{sender_map.get(turn.sender, turn.sender.capitalize())}: {turn.message}"
            
            # Check for explicit markers in assistant's past messages to provide clarity
            if turn.sender == "assistant" or turn.sender == "GEMINI_MANAGER":
                if turn.message.startswith(GEMINI_MARKER_NEED_INPUT):
                    turn_text = f"Your Previous Question to User: {turn.message.replace(GEMINI_MARKER_NEED_INPUT, '').strip()}"
                elif turn.message.startswith(GEMINI_MARKER_TASK_COMPLETE):
                     turn_text = f"Your Previous Task Completion Statement: {turn.message.replace(GEMINI_MARKER_TASK_COMPLETE, '').strip()}"
                # Add other markers if needed

            prompt_parts.append(turn_text)

        if cursor_log_content is not None: # Could be empty string if file was empty
            prompt_parts.append(f"\n--- Output from Last Cursor Tool Execution ---\n{cursor_log_content if cursor_log_content else '[No output from Cursor tool]'}")
        
        prompt_parts.append("\n--- Your Next Step ---")
        prompt_parts.append("Based on all the above, provide your next instruction OR use one of the special markers (NEED_USER_INPUT:, TASK_COMPLETE, SYSTEM_ERROR:).")
        
        return "\n".join(prompt_parts)

    def get_next_step_from_gemini(self,
                                  project_goal: str,
                                  full_conversation_history: List[Turn],
                                  current_context_summary: Optional[str],
                                  max_history_turns: int,
                                  max_context_tokens: int, # For Gemini's generate_content config
                                  cursor_log_content: Optional[str],
                                  initial_project_structure_overview: Optional[str] = None
                                  ) -> Dict[str, Any]:
        if not self.model:
            logger.error("Gemini model not initialized. Cannot get next step.")
            # Simulate a SYSTEM_ERROR response that the engine can understand
            return {
                "status": "ERROR", # Internal status for engine
                "next_step_action": "SYSTEM_ERROR", # To guide engine's state machine
                "content": f"{GEMINI_MARKER_SYSTEM_ERROR} Gemini model not initialized. API key might be missing or invalid.",
                "full_response_for_history": f"{GEMINI_MARKER_SYSTEM_ERROR} Gemini model not initialized. API key might be missing or invalid."
            }

        prompt = self._construct_prompt(
            project_goal,
            full_conversation_history,
            current_context_summary,
            max_history_turns,
            initial_project_structure_overview,
            cursor_log_content
        )
        
        # Estimate prompt tokens (very rough, for logging only)
        # A more accurate token counter would be better if hitting limits.
        estimated_tokens = len(prompt) // 4 
        logger.info(f"--- FINAL PROMPT TO LIVE GEMINI ({self.model_name}) Est. Tokens: {estimated_tokens} ---")
        # For debugging, can log parts of the prompt:
        # logger.debug(f"Prompt (first 500 chars): {prompt[:500]}")
        # logger.debug(f"Prompt (last 300 chars): {prompt[-300:]}")
        if estimated_tokens > max_context_tokens * 0.9: # Warn if close to limit
             logger.warning(f"Estimated prompt tokens ({estimated_tokens}) are close to or exceed max_context_tokens ({max_context_tokens}).")


        generation_config = genai.types.GenerationConfig(
            # max_output_tokens=self.config.get_max_output_tokens_gemini(), # Use config
            # temperature=self.config.get_temperature_gemini(),             # Use config
            # Add other relevant generation parameters from config if needed
        )
        # Ensure safety settings are reasonable if not using defaults
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

        try:
            logger.info(f"GeminiComms: Calling live Gemini API (model: {self.model_name}). Est. Prompt Tokens: {estimated_tokens}")
            response = self.model.generate_content(
                prompt,
                generation_config=generation_config,
                safety_settings=safety_settings
            )
            
            # response.text might raise ValueError if blocked, or prompt_feedback indicates block
            if response.prompt_feedback and response.prompt_feedback.block_reason:
                block_reason = response.prompt_feedback.block_reason
                block_message = f"Gemini content generation blocked. Reason: {block_reason}."
                if response.prompt_feedback.safety_ratings:
                    block_message += f" Safety Ratings: {response.prompt_feedback.safety_ratings}"
                logger.error(block_message)
                return {
                    "status": "ERROR", 
                    "next_step_action": "SYSTEM_ERROR",
                    "content": f"{GEMINI_MARKER_SYSTEM_ERROR} {block_message}",
                    "full_response_for_history": f"{GEMINI_MARKER_SYSTEM_ERROR} {block_message}"
                }

            raw_response_text = response.text.strip()
            logger.info(f"GeminiComms: Raw response from Gemini (first 300 chars): {raw_response_text[:300]}")

            # Parse the response based on markers
            if raw_response_text.startswith(GEMINI_MARKER_NEED_INPUT):
                question = raw_response_text.replace(GEMINI_MARKER_NEED_INPUT, "", 1).strip()
                return {
                    "status": "OK", # Internal status for engine
                    "next_step_action": "REQUEST_USER_INPUT",
                    "clarification_question": question,
                    "full_response_for_history": raw_response_text 
                }
            elif raw_response_text.startswith(GEMINI_MARKER_TASK_COMPLETE):
                completion_message = raw_response_text.replace(GEMINI_MARKER_TASK_COMPLETE, "", 1).strip()
                return {
                    "status": "OK",
                    "next_step_action": "TASK_COMPLETE",
                    "completion_message": completion_message,
                    "full_response_for_history": raw_response_text
                }
            elif raw_response_text.startswith(GEMINI_MARKER_SYSTEM_ERROR):
                error_detail = raw_response_text.replace(GEMINI_MARKER_SYSTEM_ERROR, "", 1).strip()
                return {
                    "status": "ERROR", # Internal status for engine
                    "next_step_action": "SYSTEM_ERROR", 
                    "content": raw_response_text, # Keep marker for history
                    "full_response_for_history": raw_response_text,
                    "error": error_detail # Specific error for engine's last_error_message
                }
            else: # Assume it's an instruction for Cursor
                return {
                    "status": "OK",
                    "next_step_action": "WRITE_TO_FILE",
                    "instruction": raw_response_text,
                    "full_response_for_history": raw_response_text
                }

        except google.api_core.exceptions.GoogleAPIError as e:
            logger.error(f"GeminiComms: Google API Error: {e}", exc_info=True)
            error_content = f"{GEMINI_MARKER_SYSTEM_ERROR} Google API Error: {type(e).__name__} - {e}"
            return {
                "status": "ERROR", 
                "next_step_action": "SYSTEM_ERROR",
                "content": error_content, 
                "full_response_for_history": error_content,
                "error": f"Google API Error: {type(e).__name__} - {e}"
            }
        except ValueError as ve: # Can be raised by response.text if content is blocked
            logger.error(f"GeminiComms: ValueError processing Gemini response (likely content blocked): {ve}", exc_info=True)
            error_content = f"{GEMINI_MARKER_SYSTEM_ERROR} Error processing Gemini response (ValueError, possibly blocked content): {ve}"
            # Try to get block reason if available
            block_reason_detail = "Unknown blocking reason."
            if hasattr(response, 'prompt_feedback') and response.prompt_feedback and response.prompt_feedback.block_reason:
                block_reason_detail = f"Reason: {response.prompt_feedback.block_reason}."
                if response.prompt_feedback.safety_ratings:
                    block_reason_detail += f" Safety Ratings: {response.prompt_feedback.safety_ratings}"
            error_content = f"{GEMINI_MARKER_SYSTEM_ERROR} Content blocked by Gemini. {block_reason_detail}"

            return {
                "status": "ERROR", 
                "next_step_action": "SYSTEM_ERROR",
                "content": error_content, 
                "full_response_for_history": error_content,
                "error": f"Content blocked by Gemini. {block_reason_detail}"
            }
        except Exception as e:
            logger.error(f"GeminiComms: Unexpected error in get_next_step_from_gemini: {e}", exc_info=True)
            error_content = f"{GEMINI_MARKER_SYSTEM_ERROR} Unexpected error: {type(e).__name__} - {e}"
            return {
                "status": "ERROR", 
                "next_step_action": "SYSTEM_ERROR",
                "content": error_content, 
                "full_response_for_history": error_content,
                "error": f"Unexpected error: {type(e).__name__} - {e}"
            }

    def summarize_conversation_history(self,
                                       history_turns: List[Turn],
                                       existing_summary: Optional[str],
                                       project_goal: str,
                                       max_tokens: int) -> Optional[str]:
        if not self.model:
            logger.error("Gemini model not initialized. Cannot summarize text.")
            return existing_summary # Return old summary if model is not working

        if not history_turns:
            logger.info("No new turns to summarize. Returning existing summary.")
            return existing_summary

        prompt_parts = ["You are a helpful AI assistant tasked with summarizing a conversation."]
        prompt_parts.append(f"The overall project goal is: {project_goal}")
        if existing_summary:
            prompt_parts.append(f"\nHere is the existing summary of the conversation so far:\n{existing_summary}")
            prompt_parts.append("\nNow, please incorporate the following new conversation turns into this summary. Create a concise, updated summary that reflects the key information and decisions from both the old summary and the new turns.")
        else:
            prompt_parts.append("\nPlease provide a concise summary of the following conversation:")
        
        prompt_parts.append("\n--- New Conversation Turns ---")
        for turn in history_turns:
            prompt_parts.append(f"[{turn.sender}]: {turn.message}")
        
        prompt_parts.append("\n--- End of New Conversation Turns ---")
        prompt_parts.append(f"Please provide the new, comprehensive summary (max {max_tokens} tokens).")
        
        summarization_prompt = "\n".join(prompt_parts)
        # logger.debug(f"Summarization prompt for Gemini: {summarization_prompt}")

        generation_config = genai.types.GenerationConfig(
            max_output_tokens=max_tokens,
            # temperature=0.5 # Slightly lower temperature for factual summarization
        )
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

        try:
            logger.info(f"Calling Gemini for summarization (model: {self.model_name}).")
            response = self.model.generate_content(
                summarization_prompt,
                generation_config=generation_config,
                safety_settings=safety_settings
            )
            if response.prompt_feedback and response.prompt_feedback.block_reason:
                logger.error(f"Summarization call blocked by Gemini. Reason: {response.prompt_feedback.block_reason}")
                return existing_summary # Return old summary on block

            new_summary = response.text.strip()
            logger.info(f"Successfully received summary from Gemini. Length: {len(new_summary)}")
            return new_summary
        except Exception as e:
            logger.error(f"Error during Gemini summarization call: {e}", exc_info=True)
            return existing_summary # Return old summary on error 