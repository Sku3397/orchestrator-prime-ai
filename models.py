from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import datetime
from enum import Enum

class OrchestratorState(Enum):
    IDLE = "IDLE"
    LOADING_PROJECT = "LOADING_PROJECT"
    PROJECT_SELECTED = "PROJECT_SELECTED"
    RUNNING_WAITING_INITIAL_GEMINI = "RUNNING_WAITING_INITIAL_GEMINI"
    RUNNING_WAITING_LOG = "RUNNING_WAITING_LOG"
    RUNNING_PROCESSING_LOG = "RUNNING_PROCESSING_LOG"
    RUNNING_CALLING_GEMINI = "RUNNING_CALLING_GEMINI"
    PAUSED_WAITING_USER_INPUT = "PAUSED_WAITING_USER_INPUT"
    TASK_COMPLETE = "TASK_COMPLETE"
    ERROR = "ERROR"

@dataclass
class Project:
    name: str
    workspace_root_path: str
    overall_goal: str
    id: Optional[str] = None  # Optional unique ID, can be generated if needed

@dataclass
class ProjectState:
    project_id: str # To link back to the Project
    conversation_history: List['Turn'] = field(default_factory=list) # Changed to List[Turn]
    current_status: str = "IDLE" # e.g., IDLE, RUNNING, PAUSED_USER_INPUT, ERROR
    current_goal: Optional[str] = None # Added current_goal
    last_instruction_sent: Optional[str] = None
    context_summary: Optional[str] = None # For future use
    pending_user_question: Optional[str] = None # Added to store the question Gemini is waiting on
    gemini_turns_since_last_summary: int = 0 # Added for summarization logic
    # TODO: Add any other state variables needed, e.g., current_task_id, timestamps 

@dataclass
class Turn:
    sender: str  # e.g., "user", "gemini", "system"
    message: str
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    metadata: Optional[Dict[str, Any]] = None # For any extra info like tool calls, etc. 