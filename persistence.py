import json
import os
import uuid # For generating project IDs
from typing import List, Optional, Dict, Any 
from .models import Project, ProjectState

APP_DATA_DIR = "app_data"
PROJECTS_FILE = os.path.join(APP_DATA_DIR, "projects.json")
PROJECT_STATE_DIR_NAME = ".orchestrator_state"
PROJECT_STATE_FILE_NAME = "state.json"

def _ensure_app_data_dir_exists():
    if not os.path.exists(APP_DATA_DIR):
        os.makedirs(APP_DATA_DIR)

def _ensure_project_state_dir_exists(workspace_root_path: str) -> str:
    state_dir = os.path.join(workspace_root_path, PROJECT_STATE_DIR_NAME)
    if not os.path.exists(state_dir):
        os.makedirs(state_dir)
    return state_dir

def load_projects() -> List[Project]:
    _ensure_app_data_dir_exists()
    if not os.path.exists(PROJECTS_FILE):
        return []
    try:
        with open(PROJECTS_FILE, 'r') as f:
            projects_data = json.load(f)
        return [Project(**data) for data in projects_data]
    except (json.JSONDecodeError, TypeError) as e:
        print(f"Error loading projects from {PROJECTS_FILE}: {e}")
        return []

def save_projects(projects: List[Project]):
    _ensure_app_data_dir_exists()
    try:
        projects_data = [vars(p) for p in projects]
        with open(PROJECTS_FILE, 'w') as f:
            json.dump(projects_data, f, indent=4)
    except (IOError, TypeError) as e:
        print(f"Error saving projects to {PROJECTS_FILE}: {e}")

def add_project(name: str, workspace_root_path: str, overall_goal: str) -> Optional[Project]:
    projects = load_projects()
    # Check for duplicate names or paths if necessary, though not strictly required here
    new_project_id = str(uuid.uuid4())
    new_project = Project(name=name, workspace_root_path=workspace_root_path, overall_goal=overall_goal, id=new_project_id)
    projects.append(new_project)
    save_projects(projects)
    # Initialize state for the new project
    initial_state = ProjectState(project_id=new_project_id)
    save_project_state(new_project, initial_state)
    return new_project

def get_project_by_id(project_id: str) -> Optional[Project]:
    projects = load_projects()
    for p in projects:
        if p.id == project_id:
            return p
    return None

def get_project_by_name(project_name: str) -> Optional[Project]:
    projects = load_projects()
    for p in projects:
        if p.name == project_name:
            return p
    return None

def load_project_state(project: Project) -> Optional[ProjectState]:
    if not project or not project.workspace_root_path:
        print("Error: Invalid project provided for loading state.")
        return None
    state_dir = _ensure_project_state_dir_exists(project.workspace_root_path)
    state_file_path = os.path.join(state_dir, PROJECT_STATE_FILE_NAME)

    if not os.path.exists(state_file_path):
        # If state file doesn't exist, create a default one
        print(f"State file not found for project {project.name}. Creating default state.")
        default_state = ProjectState(project_id=project.id)
        save_project_state(project, default_state)
        return default_state
    
    try:
        with open(state_file_path, 'r') as f:
            state_data = json.load(f)
        return ProjectState(**state_data)
    except (json.JSONDecodeError, TypeError, FileNotFoundError) as e:
        print(f"Error loading project state for {project.name} from {state_file_path}: {e}")
        # Fallback to a default state if loading fails critically
        print(f"Returning a default state for project {project.name}.")
        return ProjectState(project_id=project.id)

def save_project_state(project: Project, state: ProjectState):
    if not project or not project.workspace_root_path:
        print("Error: Invalid project provided for saving state.")
        return
    
    state_dir = _ensure_project_state_dir_exists(project.workspace_root_path)
    state_file_path = os.path.join(state_dir, PROJECT_STATE_FILE_NAME)

    try:
        with open(state_file_path, 'w') as f:
            json.dump(vars(state), f, indent=4)
    except (IOError, TypeError) as e:
        print(f"Error saving project state for {project.name} to {state_file_path}: {e}")

# Example usage (optional, for testing)
if __name__ == '__main__':
    # Clear existing projects for a clean test
    if os.path.exists(PROJECTS_FILE):
        os.remove(PROJECTS_FILE)

    # Test project creation
    project1 = add_project("Test Project 1", "./test_workspace_1", "Automate testing.")
    project2 = add_project("Test Project 2", "./test_workspace_2", "Develop new UI.")

    if project1:
        print(f"Created Project 1: {project1}")
        state1 = load_project_state(project1)
        if state1:
            state1.current_status = "RUNNING"
            state1.conversation_history.append({"sender": "user", "message": "Start the process"})
            save_project_state(project1, state1)
            print(f"Saved state for Project 1: {state1}")
            loaded_state1 = load_project_state(project1)
            print(f"Reloaded state for Project 1: {loaded_state1}")

    all_projects = load_projects()
    print(f"All projects: {all_projects}")

    # Test loading a project that might not have a state file initially
    # (Assuming ./test_workspace_3 does not have .orchestrator_state/state.json)
    # mock_project_no_state = Project(name="No State Project", workspace_root_path="./test_workspace_3", overall_goal="Test state creation", id=str(uuid.uuid4()))
    # if not os.path.exists("./test_workspace_3"):
    #     os.makedirs("./test_workspace_3")
    # state_for_no_state_project = load_project_state(mock_project_no_state)
    # print(f"State for no-state project (should be default): {state_for_no_state_project}")
    # if state_for_no_state_project:
    #    save_project_state(mock_project_no_state, state_for_no_state_project) # Save it for next time

    print("Persistence tests complete.") 