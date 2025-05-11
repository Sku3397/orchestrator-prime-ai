import logging
import time
from typing import Dict, Any, Optional, List

# Assuming Turn is defined elsewhere, e.g., in models.py
# If not, a placeholder or simplified version might be needed here.
# For now, let's assume it's available or we'll use Any.
try:
    from models import Turn # Try to import the actual Turn
except ImportError:
    Turn = Any # Fallback if models.Turn is not found in this context

logger = logging.getLogger(__name__)

class MockGeminiCommunicatorBase:
    """Base class for mock Gemini communicators."""
    def __init__(self, mock_type: str = "BASE_MOCK", details: Optional[Dict[str, Any]] = None):
        self.mock_type = mock_type
        self.details = details if details is not None else {}
        logger.info(f"MOCK GeminiCommunicator INSTANTIATED. Type: '{self.mock_type}', Details: {self.details}")

    def get_next_step_from_gemini(
        self,
        project_goal: str,
        full_conversation_history: List[Turn],
        current_context_summary: Optional[str],
        max_history_turns: int,
        max_context_tokens: int, # Added to match real signature
        cursor_log_content: Optional[str],
        initial_project_structure_overview: Optional[str] = None
    ) -> Dict[str, Any]:
        logger.info(f"MOCK get_next_step_from_gemini called. Type: '{self.mock_type}'. Goal: '{project_goal[:30]}...'")
        time.sleep(0.05) # Simulate some processing time
        return self._handle_mock_response()

    def _handle_mock_response(self) -> Dict[str, Any]:
        # Default mock behavior
        return {"status": "INSTRUCTION", "content": "Default mock instruction from MockGeminiCommunicatorBase."}

    def summarize_text(self, text_to_summarize: str, max_length: Optional[int] = None) -> Optional[str]:
        logger.info(f"MOCK summarize_text called. Type: '{self.mock_type}'. Text len: {len(text_to_summarize)}")
        time.sleep(0.05)
        summary = f"Mock summary of text (first 50 chars): {text_to_summarize[:50]}..."
        if max_length:
            summary = summary[:max_length]
        return summary

    def summarize_conversation_history(
        self,
        history_turns: List[Turn],
        existing_summary: Optional[str],
        project_goal: str, 
        max_tokens: Optional[int] = None
    ) -> Optional[str]:
        logger.info(f"MOCK summarize_conversation_history called. Type: '{self.mock_type}'. Turns: {len(history_turns)}, Goal: {project_goal[:30]}")
        time.sleep(0.05)
        # Simple mock summary, could be made more sophisticated if needed by tests
        new_summary = f"Mock summary based on {len(history_turns)} new turns. Existing summary: {'Yes' if existing_summary else 'No'}. Goal: {project_goal[:20]}..."
        if max_tokens:
            return new_summary[:max_tokens]
        return new_summary

class StandardInstructionMock(MockGeminiCommunicatorBase):
    def __init__(self, details: Optional[Dict[str, Any]] = None):
        super().__init__(mock_type="STANDARD_INSTRUCTION", details=details)

    def _handle_mock_response(self) -> Dict[str, Any]:
        instruction = self.details.get("instruction", "Mocked standard instruction from StandardInstructionMock.")
        logger.info(f"MOCK (StandardInstructionMock): Returning INSTRUCTION: '{instruction}'")
        # Ensure the response structure matches what the engine expects for writing to file
        return {
            "status": "SUCCESS", # Or whatever status indicates a valid instruction from Gemini
            "instruction": instruction,
            "next_step_action": "WRITE_TO_FILE",
            "full_response_for_history": instruction # For history, the instruction itself is fine for mocks
        }

class UserQuestionMock(MockGeminiCommunicatorBase):
    def __init__(self, details: Optional[Dict[str, Any]] = None):
        super().__init__(mock_type="USER_QUESTION", details=details)

    def _handle_mock_response(self) -> Dict[str, Any]:
        question = self.details.get("question", "Mocked user question from UserQuestionMock?")
        logger.info(f"MOCK (UserQuestionMock): Returning USER_QUESTION: '{question}'")
        # Ensure the response structure matches what the engine expects
        return {
            "status": "SUCCESS", # Or appropriate status
            "clarification_question": question,
            "next_step_action": "REQUEST_USER_INPUT",
            "full_response_for_history": f"NEED_USER_INPUT: {question}"
        }

class ErrorMock(MockGeminiCommunicatorBase):
    def __init__(self, details: Optional[Dict[str, Any]] = None):
        super().__init__(mock_type="ERROR_RESPONSE", details=details)

    def _handle_mock_response(self) -> Dict[str, Any]:
        error_message = self.details.get("error", "Mocked error from ErrorMock.")
        logger.info(f"MOCK (ErrorMock): Returning ERROR_RESPONSE: '{error_message}'")
        # Ensure the response structure matches
        return {
            "status": "ERROR", 
            "error": error_message, 
            "content": error_message, # For compatibility if engine checks 'content' on error
            "error_type": "MockedError",
            "next_step_action": "FATAL_ERROR", # Or some other appropriate action for engine
            "full_response_for_history": f"SYSTEM_ERROR: {error_message}"
        }

# Factory function to get a mock communicator
def get_mock_communicator(mock_type: str, details: Optional[Dict[str, Any]] = None) -> MockGeminiCommunicatorBase:
    logger.info(f"Mock factory called for type: '{mock_type}', details: {details}")
    if mock_type == "STANDARD_INSTRUCTION":
        return StandardInstructionMock(details)
    elif mock_type == "USER_QUESTION":
        return UserQuestionMock(details)
    elif mock_type == "ERROR_RESPONSE":
        return ErrorMock(details)
    else:
        logger.warning(f"Unknown mock_type '{mock_type}' requested. Returning base mock.")
        return MockGeminiCommunicatorBase(mock_type="UNKNOWN_FALLBACK", details=details) 