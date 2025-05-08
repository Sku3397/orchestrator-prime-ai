from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import datetime

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
    last_instruction_sent: Optional[str] = None
    context_summary: Optional[str] = None # For future use
    # TODO: Add any other state variables needed, e.g., current_task_id, timestamps 

@dataclass
class Turn:
    sender: str  # e.g., "user", "gemini", "system"
    message: str
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    metadata: Optional[Dict[str, Any]] = None # For any extra info like tool calls, etc. 