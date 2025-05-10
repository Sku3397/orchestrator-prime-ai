
import google.api_core.exceptions # For simulating specific API errors
import logging
import time
from typing import Optional, Dict, Any, List

# These imports are assumed to be resolvable in the context where this mock
# gemini_comms.py file will be written and then imported by the test script.
# If ConfigManager or Turn are complex, the test script might need to
# ensure dummy versions are available in sys.path if the real ones aren't.
from config_manager import ConfigManager
from models import Turn

# Using __name__ will make the logger name 'gemini_comms' when this is written to gemini_comms.py
logger = logging.getLogger(__name__)

# Placeholders that will be replaced by str.format() from the test script
MOCK_DETAILS_HOLDER = '''{'instruction': 'TC20 - Instruction 1 / 3'}''' # Changed to triple single quotes

# Markers used by the engine to parse Gemini's special responses
GEMINI_MARKER_NEED_INPUT = "NEED_USER_INPUT:"
GEMINI_MARKER_TASK_COMPLETE = "TASK_COMPLETE"
GEMINI_MARKER_SYSTEM_ERROR = "SYSTEM_ERROR:"

# For the mock, the exact content of CURSOR_SOP_PROMPT might not be critical
# unless the mock logic itself needs to parse or use it.
# Ensure this is a clean, simple multi-line string.
CURSOR_SOP_PROMPT = '''This is a minimal placeholder for CURSOR_SOP_PROMPT.
Its full content is not essential for the mock's internal logic,
but the variable should exist if any code in the main engine
(not this mock) tries to import it from a 'gemini_comms' module.
(Ideally, the main engine imports it from its own config or constants).'''

class GeminiCommunicator:
    def __init__(self):
        self.mock_type = "STANDARD_INSTRUCTION" # CHANGED
        # MOCK_DETAILS_HOLDER will be a string representation of a dict, or 'None'
        # We need to evaluate it safely if it's a dict string.
        details_str = MOCK_DETAILS_HOLDER
        if details_str and details_str != 'None':
            try:
                # Safely evaluate the string representation of the dictionary
                import ast
                self.details = ast.literal_eval(details_str)
            except (ValueError, SyntaxError) as e:
                logger.error(f"MOCK GeminiCommunicator: Error evaluating MOCK_DETAILS_HOLDER '{details_str}': {e}")
                self.details = {} # Default to empty dict on error
        else:
            self.details = {}


        try:
            # This will attempt to use the *actual* ConfigManager if this mock
            # is run in an environment where config_manager.py is importable.
            self.config = ConfigManager()
            self.model_name = self.config.get_gemini_model()
        except Exception as e:
            logger.error(f"MOCK GeminiCommunicator: Error loading real ConfigManager in mock: {e}")
            self.model_name = "mock_model_due_to_config_error"
        logger.info(f"MOCK GeminiCommunicator INSTANTIATED. Mock Type: '{self.mock_type}', Details: {self.details}")

    def get_next_step_from_gemini(self,
                                  project_goal: str,
                                  full_conversation_history: List[Turn],
                                  current_context_summary: str,
                                  max_history_turns: int,
                                  max_context_tokens: int,
                                  cursor_log_content: Optional[str],
                                  initial_project_structure_overview: Optional[str] = None
                                  ) -> Dict[str, Any]:
        logger.info(f"MOCK get_next_step_from_gemini called. Type: '{self.mock_type}'")
        time.sleep(0.05) # Minimal simulated delay

        # Use direct string comparisons for mock_type
        if self.mock_type == "ERROR_API_AUTH":
            logger.error("MOCK: Simulating API Auth Error (PermissionDenied)")
            raise google.api_core.exceptions.PermissionDenied("Mocked PermissionDenied: API key error.")
        elif self.mock_type == "ERROR_NON_AUTH":
            logger.error("MOCK: Simulating Non-Auth Google API Error (InvalidArgument)")
            raise google.api_core.exceptions.InvalidArgument("Mocked InvalidArgument: Non-auth API error.")
        elif self.mock_type == "NEED_INPUT":
            question = "Default mock question from Gemini?"
            if isinstance(self.details, dict) and "question" in self.details:
                question = self.details["question"]
            logger.info(f"MOCK: Returning NEED_INPUT with: {question}")
            return {"status": "NEED_INPUT", "content": question}
        elif self.mock_type == "TASK_COMPLETE":
            logger.info("MOCK: Returning TASK_COMPLETE")
            return {"status": "COMPLETE", "content": "Mocked: Project goal achieved."}
        elif self.mock_type == "STANDARD_INSTRUCTION":
            instruction = "Mocked standard instruction."
            if isinstance(self.details, dict) and "instruction" in self.details:
                instruction = self.details["instruction"]
            logger.info(f"MOCK: Returning STANDARD_INSTRUCTION: {instruction}")
            return {"status": "INSTRUCTION", "content": instruction}
        elif self.mock_type == "SYSTEM_ERROR_GEMINI": # For testing engine's handling of Gemini system errors
            error_message = "Simulated internal Gemini system error."
            if isinstance(self.details, dict) and "error_message" in self.details:
                error_message = self.details["error_message"]
            logger.info(f"MOCK: Returning SYSTEM_ERROR: {error_message}")
            # The engine expects the marker *within the content* for this specific case
            return {"status": "INSTRUCTION", "content": f"{GEMINI_MARKER_SYSTEM_ERROR} {error_message}"}

        # Fallback for any unhandled mock_type
        logger.warning(f"MOCK: Unknown mock type '{self.mock_type}'. Returning default instruction.")
        return {"status": "INSTRUCTION", "content": "Default mock instruction (unhandled mock type)."}

    def summarize_text(self, text_to_summarize: str, max_summary_tokens: int = 1000) -> Optional[str]:
        logger.info(f"MOCK summarize_text CALLED. Text to summarize length: {len(text_to_summarize)}. Max tokens: {max_summary_tokens}.")
        # This path is relative to where the mock gemini_comms.py will be executed from (workspace root)
        summarizer_log_file = "temp_summarizer_input.txt"
        try:
            with open(summarizer_log_file, "w", encoding='utf-8') as f:
                f.write(text_to_summarize)
            logger.info(f"MOCK summarize_text: Wrote input to {summarizer_log_file}")
        except Exception as e:
            logger.error(f"MOCK summarize_text: Failed to write to {summarizer_log_file}: {e}")
        
        time.sleep(0.05) # Simulate some processing
        return f"[Mocked Summary of input with length: {len(text_to_summarize)} chars. Max tokens: {max_summary_tokens}]"

