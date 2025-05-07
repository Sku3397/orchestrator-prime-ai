from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class Project:
    name: str
    workspace_root_path: str
    overall_goal: str
    id: Optional[str] = None  # Optional unique ID, can be generated if needed

@dataclass
class ProjectState:
    project_id: str # To link back to the Project
    conversation_history: List[dict] = field(default_factory=list) # e.g., [{'sender': 'user', 'message': '...'}, {'sender': 'gemini', 'message': '...'}]
    current_status: str = "IDLE" # e.g., IDLE, RUNNING, PAUSED_USER_INPUT, ERROR
    last_instruction_sent: Optional[str] = None
    context_summary: Optional[str] = None # For future use
    # TODO: Add any other state variables needed, e.g., current_task_id, timestamps 