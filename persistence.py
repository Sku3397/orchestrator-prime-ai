import json
import os
import uuid # For generating project IDs
from typing import List, Optional, Dict, Any 
from models import Project, ProjectState, Turn
from dataclasses import asdict
import logging # Added

# Get logger instance
logger = logging.getLogger("orchestrator_prime")

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
            logger.info(f"Created application data directory: {APP_DATA_DIR}")
        except OSError as e:
            logger.critical(f"Could not create application data directory {APP_DATA_DIR}: {e}", exc_info=True)
            raise PersistenceError(f"Failed to create {APP_DATA_DIR}: {e}") from e

def _ensure_project_state_dir_exists(workspace_root_path: str) -> Optional[str]:
    if not os.path.isabs(workspace_root_path):
        # Making this a warning, as maybe relative paths could be resolved, but generally risky.
        logger.warning(f"workspace_root_path '{workspace_root_path}' should ideally be an absolute path.")
        # For now, let's try to proceed by making it absolute relative to cwd
        # This might not be the intended behavior in all cases.
        workspace_root_path = os.path.abspath(workspace_root_path)
        logger.warning(f"Resolved relative workspace path to absolute: {workspace_root_path}")
        # Alternatively, raise PersistenceError here if absolute paths are mandatory.
        # raise PersistenceError(f"workspace_root_path '{workspace_root_path}' must be an absolute path.")
        # return None

    state_dir = os.path.join(workspace_root_path, PROJECT_STATE_DIR_NAME)
    if not os.path.exists(state_dir):
        try:
            os.makedirs(state_dir)
            logger.info(f"Created project state directory: {state_dir}")
        except OSError as e:
            logger.error(f"Could not create project state directory {state_dir}: {e}", exc_info=True)
            return None # Indicate failure
    return state_dir

def load_projects() -> List[Project]:
    try:
        _ensure_app_data_dir_exists() 
    except PersistenceError:
        # Error already logged in _ensure_app_data_dir_exists
        return [] # Cannot proceed without app data dir

    if not os.path.exists(PROJECTS_FILE):
        try:
            with open(PROJECTS_FILE, 'w') as f:
                json.dump([], f)
            logger.info(f"Created empty projects file: {PROJECTS_FILE}")
            return []
        except IOError as e:
            logger.error(f"Could not create empty projects file {PROJECTS_FILE}: {e}", exc_info=True)
            return []
    try:
        with open(PROJECTS_FILE, 'r') as f:
            projects_data = json.load(f)
        # Add validation here if needed (e.g., check if data is a list of dicts)
        return [Project(**data) for data in projects_data]
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from {PROJECTS_FILE}. Returning empty project list. Error: {e}", exc_info=True)
        return []
    except TypeError as e: 
        logger.error(f"Type error loading projects from {PROJECTS_FILE}. Data might be malformed. Error: {e}", exc_info=True)
        return []
    except IOError as e: 
        logger.error(f"Could not read projects file {PROJECTS_FILE}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.critical(f"Unexpected error loading projects from {PROJECTS_FILE}: {e}", exc_info=True)
        return []

def save_projects(projects: List[Project]):
    try:
        _ensure_app_data_dir_exists()
    except PersistenceError as e:
        logger.critical(f"Cannot save projects, app_data directory inaccessible: {e}")
        raise PersistenceError(f"Cannot save projects, app_data directory inaccessible: {e}") from e

    projects_data = [asdict(p) for p in projects]
    try:
        with open(PROJECTS_FILE, 'w') as f:
            json.dump(projects_data, f, indent=4)
        logger.info(f"Saved {len(projects)} projects to {PROJECTS_FILE}")
    except (IOError, TypeError) as e: 
        logger.error(f"Failed to save projects to {PROJECTS_FILE}: {e}", exc_info=True)
        raise PersistenceError(f"Failed to save projects: {e}") from e
    except Exception as e:
        logger.critical(f"Unexpected error saving projects to {PROJECTS_FILE}: {e}", exc_info=True)
        raise PersistenceError(f"Unexpected error saving projects: {e}") from e

def load_project_state(project: Project) -> Optional[ProjectState]:
    if not project or not project.workspace_root_path:
        logger.error("Invalid project provided (missing or no workspace_root_path) for loading state.")
        return None
    
    # Ensure workspace path is absolute for reliable state dir calculation
    abs_workspace_path = os.path.abspath(project.workspace_root_path)
    state_dir = os.path.join(abs_workspace_path, PROJECT_STATE_DIR_NAME)
    state_file_path = os.path.join(state_dir, PROJECT_STATE_FILE_NAME)

    if not os.path.exists(state_file_path):
        logger.info(f"State file not found for project '{project.name}' at {state_file_path}. Returning None (new state will be created by engine if needed).")
        return None 
    
    logger.debug(f"Attempting to load project state for '{project.name}' from {state_file_path}")
    try:
        with open(state_file_path, 'r') as f:
            state_data = json.load(f)
        
        # Rehydrate Turn objects from dicts
        if 'conversation_history' in state_data and isinstance(state_data['conversation_history'], list):
            hydrated_history = []
            for turn_data in state_data['conversation_history']:
                if isinstance(turn_data, dict):
                    hydrated_history.append(Turn(**turn_data))
                else:
                    logger.warning(f"Skipping invalid item in conversation_history for project '{project.name}': {turn_data}")
            state_data['conversation_history'] = hydrated_history
        else:
            state_data['conversation_history'] = [] # Ensure it exists as a list
            
        # Basic validation
        if not state_data.get('project_id'):
             logger.warning(f"Loaded state for project '{project.name}' is missing 'project_id'. Attempting to use current project ID: {project.id}")
             state_data['project_id'] = project.id # Try to fix it

        project_state = ProjectState(**state_data)
        logger.info(f"Successfully loaded project state for '{project.name}'. Status: {project_state.current_status}, History turns: {len(project_state.conversation_history)}")
        return project_state
        
    except FileNotFoundError: # Should be caught by exists() check, but safeguard
        logger.error(f"State file {state_file_path} vanished before read for '{project.name}'.")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode state file {state_file_path} for '{project.name}'. Error: {e}", exc_info=True)
        return None
    except TypeError as e: 
        logger.error(f"Type error loading project state for '{project.name}' from {state_file_path}. Data malformed? Error: {e}", exc_info=True)
        return None
    except IOError as e: 
        logger.error(f"Could not read state file {state_file_path} for '{project.name}': {e}", exc_info=True)
        return None
    except Exception as e: 
        logger.critical(f"Unexpected error loading project state for '{project.name}' from {state_file_path}: {e}", exc_info=True)
        return None

def save_project_state(project: Project, state: ProjectState):
    if not project or not project.workspace_root_path:
        logger.error("Invalid project provided (missing or no workspace_root_path) for saving state.")
        raise PersistenceError("Invalid project for saving state.")
    if not state:
        logger.error(f"Invalid state object provided for saving project '{project.name}'.")
        raise PersistenceError("Invalid state object provided.")

    # Use absolute path for reliability
    abs_workspace_path = os.path.abspath(project.workspace_root_path)
    state_dir = _ensure_project_state_dir_exists(abs_workspace_path)
    if not state_dir: 
        logger.error(f"Failed to ensure state directory exists for project '{project.name}' at '{abs_workspace_path}'. Cannot save state.")
        raise PersistenceError(f"Failed to create/access state directory for {project.name}")

    state_file_path = os.path.join(state_dir, PROJECT_STATE_FILE_NAME)
    logger.debug(f"Attempting to save project state for '{project.name}' to {state_file_path}")
    try:
        # Convert state (including Turn objects) to dict for JSON serialization
        state_data = asdict(state)
        with open(state_file_path, 'w') as f:
            json.dump(state_data, f, indent=4)
        logger.info(f"Successfully saved project state for '{project.name}' (Status: {state.current_status})")
    except (IOError, TypeError) as e: 
        logger.error(f"Failed to save project state for '{project.name}' to {state_file_path}: {e}", exc_info=True)
        raise PersistenceError(f"Failed to save project state for {project.name}: {e}") from e
    except Exception as e: 
        logger.critical(f"Unexpected error saving project state for '{project.name}' to {state_file_path}: {e}", exc_info=True)
        raise PersistenceError(f"Unexpected error saving project state for {project.name}: {e}") from e

def add_project(project_details: Project) -> Optional[Project]: # Modified to take Project object
    if not isinstance(project_details, Project):
        logger.error(f"Invalid input to add_project. Expected Project object, got {type(project_details)}")
        return None

    name = project_details.name
    workspace_root_path = project_details.workspace_root_path
    overall_goal = project_details.overall_goal

    if not os.path.isabs(workspace_root_path):
        logger.warning(f"Project '{name}' workspace path '{workspace_root_path}' is not absolute. Resolving relative to current directory. Consider using absolute paths.")
        workspace_root_path = os.path.abspath(workspace_root_path)
        logger.info(f"Resolved workspace path for '{name}' to: {workspace_root_path}")

    try:
        projects = load_projects()
    except PersistenceError as e:
        logger.error(f"Cannot add project '{name}', failed to load existing projects: {e}", exc_info=True)
        return None 

    if any(p.name == name for p in projects):
        logger.info(f"Project with name '{name}' already exists. Returning existing project.")
        return next((p for p in projects if p.name == name), None)

    # Assign an ID if the input project doesn't have one
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