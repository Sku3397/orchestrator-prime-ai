import json
import os
import uuid # For generating project IDs
from typing import List, Optional, Dict, Any 
from models import Project, ProjectState, Turn
from dataclasses import asdict

APP_DATA_DIR = "app_data"
PROJECTS_FILE = os.path.join(APP_DATA_DIR, "projects.json")
PROJECT_STATE_DIR_NAME = ".orchestrator_state"
PROJECT_STATE_FILE_NAME = "state.json"

class PersistenceError(Exception):
    """Custom exception for persistence layer errors."""
    pass

def _ensure_app_data_dir_exists():
    if not os.path.exists(APP_DATA_DIR):
        try:
            os.makedirs(APP_DATA_DIR)
            print(f"Created directory: {APP_DATA_DIR}")
        except OSError as e:
            print(f"CRITICAL PERSISTENCE ERROR: Could not create directory {APP_DATA_DIR}: {e}")
            raise PersistenceError(f"Failed to create {APP_DATA_DIR}: {e}") from e

def _ensure_project_state_dir_exists(workspace_root_path: str) -> Optional[str]:
    if not os.path.isabs(workspace_root_path):
        print(f"PERSISTENCE ERROR: workspace_root_path '{workspace_root_path}' must be an absolute path.")
        return None
    state_dir = os.path.join(workspace_root_path, PROJECT_STATE_DIR_NAME)
    if not os.path.exists(state_dir):
        try:
            os.makedirs(state_dir)
            print(f"Created directory: {state_dir}")
        except OSError as e:
            print(f"PERSISTENCE ERROR: Could not create directory {state_dir}: {e}")
            return None 
    return state_dir

def load_projects() -> List[Project]:
    try:
        _ensure_app_data_dir_exists() 
    except PersistenceError:
        return [] 

    if not os.path.exists(PROJECTS_FILE):
        try:
            with open(PROJECTS_FILE, 'w') as f:
                json.dump([], f)
            print(f"Created empty projects file: {PROJECTS_FILE}")
            return []
        except IOError as e:
            print(f"PERSISTENCE ERROR: Could not create empty projects file {PROJECTS_FILE}: {e}")
            return []
    try:
        with open(PROJECTS_FILE, 'r') as f:
            projects_data = json.load(f)
        return [Project(**data) for data in projects_data]
    except json.JSONDecodeError as e:
        print(f"PERSISTENCE ERROR: Failed to decode {PROJECTS_FILE}. Returning empty list. Error: {e}")
        return []
    except TypeError as e: 
        print(f"PERSISTENCE ERROR: Type error loading projects from {PROJECTS_FILE}. Data might be malformed. Error: {e}")
        return []
    except IOError as e: 
        print(f"PERSISTENCE ERROR: Could not read projects file {PROJECTS_FILE}: {e}")
        return []

def save_projects(projects: List[Project]):
    try:
        _ensure_app_data_dir_exists()
    except PersistenceError:
        raise PersistenceError("Cannot save projects, app_data directory inaccessible.")

    projects_data = [asdict(p) for p in projects]
    try:
        with open(PROJECTS_FILE, 'w') as f:
            json.dump(projects_data, f, indent=4)
    except (IOError, TypeError) as e: 
        print(f"PERSISTENCE ERROR: Failed to save projects to {PROJECTS_FILE}: {e}")
        raise PersistenceError(f"Failed to save projects: {e}") from e

def load_project_state(project: Project) -> Optional[ProjectState]:
    if not project or not project.workspace_root_path:
        print("PERSISTENCE ERROR: Invalid project (no workspace_root_path) for loading state.")
        return None
    
    state_dir = os.path.join(project.workspace_root_path, PROJECT_STATE_DIR_NAME)
    state_file_path = os.path.join(state_dir, PROJECT_STATE_FILE_NAME)

    if not os.path.exists(state_file_path):
        print(f"State file not found for project '{project.name}' at {state_file_path}. Returning None.")
        return None 
    
    try:
        with open(state_file_path, 'r') as f:
            state_data = json.load(f)
        
        if 'conversation_history' in state_data:
            state_data['conversation_history'] = [Turn(**turn_data) for turn_data in state_data['conversation_history']]
            
        return ProjectState(**state_data)
    except FileNotFoundError: 
        print(f"PERSISTENCE ERROR: State file {state_file_path} vanished before read for '{project.name}'.")
        return None
    except json.JSONDecodeError as e:
        print(f"PERSISTENCE ERROR: Failed to decode state file {state_file_path} for '{project.name}'. Error: {e}")
        return None
    except TypeError as e: 
        print(f"PERSISTENCE ERROR: Type error loading project state for '{project.name}' from {state_file_path}. Data malformed. Error: {e}")
        return None
    except IOError as e: 
        print(f"PERSISTENCE ERROR: Could not read state file {state_file_path} for '{project.name}': {e}")
        return None
    except Exception as e: 
        print(f"PERSISTENCE ERROR: Unexpected error loading project state for '{project.name}': {e}")
        return None

def save_project_state(project: Project, state: ProjectState):
    if not project or not project.workspace_root_path:
        print("PERSISTENCE ERROR: Invalid project (no workspace_root_path) for saving state.")
        raise PersistenceError("Invalid project for saving state.")
    if not state:
        print(f"PERSISTENCE ERROR: Invalid state object provided for project '{project.name}'.")
        raise PersistenceError("Invalid state object provided.")

    state_dir = _ensure_project_state_dir_exists(project.workspace_root_path)
    if not state_dir: 
        print(f"PERSISTENCE ERROR: Failed to ensure state directory for project '{project.name}'. Cannot save state.")
        raise PersistenceError(f"Failed to create/access state directory for {project.name}")

    state_file_path = os.path.join(state_dir, PROJECT_STATE_FILE_NAME)
    state_data = asdict(state)
    try:
        with open(state_file_path, 'w') as f:
            json.dump(state_data, f, indent=4)
    except (IOError, TypeError) as e: 
        print(f"PERSISTENCE ERROR: Failed to save project state for '{project.name}' to {state_file_path}: {e}")
        raise PersistenceError(f"Failed to save project state for {project.name}: {e}") from e
    except Exception as e: 
        print(f"PERSISTENCE ERROR: Unexpected error saving project state for '{project.name}': {e}")
        raise PersistenceError(f"Unexpected error saving project state for {project.name}: {e}") from e

def add_project(name: str, workspace_root_path: str, overall_goal: str) -> Optional[Project]:
    if not os.path.isabs(workspace_root_path):
        print(f"PERSISTENCE INFO: workspace_root_path '{workspace_root_path}' for project '{name}' must be an absolute path. Project not added.")
        return None 
        
    try:
        projects = load_projects()
    except PersistenceError as e:
        print(f"PERSISTENCE ERROR: Cannot add project, failed to load existing projects: {e}")
        return None 

    if any(p.name == name for p in projects):
        print(f"PERSISTENCE INFO: Project with name '{name}' already exists.")
        return next((p for p in projects if p.name == name), None)

    new_project = Project(name=name, workspace_root_path=workspace_root_path, overall_goal=overall_goal)
    projects.append(new_project)
    try:
        save_projects(projects)
        print(f"Added project: {name}")
        return new_project
    except PersistenceError as e:
        print(f"PERSISTENCE ERROR: Failed to save new project '{name}': {e}")
        return None 

def get_project_by_id(project_id: str) -> Optional[Project]:
    projects = load_projects() 
    for p in projects:
        if p.project_id == project_id:
            return p
    return None

def get_project_by_name(project_name: str) -> Optional[Project]:
    projects = load_projects()
    for p in projects:
        if p.name == project_name:
            return p
    return None

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