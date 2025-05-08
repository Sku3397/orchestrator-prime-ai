import os
import sys
import time

# Adjust path to import from parent directory if script is in a subfolder
# sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from engine import OrchestrationEngine, EngineState
from models import Project, ProjectState, Turn
from config_manager import ConfigManager
# from persistence import save_project, delete_project_by_id # For cleanup
import uuid

TEMP_WORKSPACE_NAME = "temp_test_workspace_autotest"

def setup_dummy_workspace():
    if not os.path.exists(TEMP_WORKSPACE_NAME):
        os.makedirs(TEMP_WORKSPACE_NAME)
    
    subdirs = ["src", "data", "docs", "another_dir1", "another_dir2", "another_dir3"]
    for sd in subdirs:
        os.makedirs(os.path.join(TEMP_WORKSPACE_NAME, sd), exist_ok=True)
        
    files = {
        "main.py": "# Main app",
        "utils.py": "# Utility functions",
        "config.py": "# Config loader",
        "requirements.txt": "requests\ncustomtkinter",
        "README.md": "# Test Project",
        "NOTES.txt": "Some notes here",
        ".gitignore": "*.pyc\n__pycache__/",
        "data/file1.csv": "col1,col2\n1,2",
        "src/module1.py": "# Module 1 code"
    }
    for file_path, content in files.items():
        full_path = os.path.join(TEMP_WORKSPACE_NAME, file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True) # Ensure parent dir exists for nested files
        with open(full_path, "w") as f:
            f.write(content)
    print(f"Dummy workspace '{TEMP_WORKSPACE_NAME}' created/verified.")

def run_test():
    print("--- Starting Autonomous Context Test ---")
    setup_dummy_workspace()

    # Create a dummy project config
    test_project_id = str(uuid.uuid4())
    test_project = Project(
        id=test_project_id,
        name="AutonomousContextTestProject",
        workspace_root_path=os.path.abspath(TEMP_WORKSPACE_NAME),
        overall_goal="Test the initial project structure context for Gemini."
    )
    # Save project to projects.json so engine can potentially load it if needed by internal logic,
    # though we will set it directly.
    # Note: persistence.add_project might be better if it handles unique IDs/names.
    # For this test, direct manipulation and cleanup is fine.
    # save_project(test_project) 

    print(f"Test Project Details: ID={test_project.id}, Name={test_project.name}, Path={test_project.workspace_root_path}")

    # Initialize engine (without GUI callback for this test)
    engine_instance = OrchestrationEngine(gui_update_callback=None)
    
    # Manually set the project and its state for the test
    engine_instance.current_project = test_project
    engine_instance.current_project_state = ProjectState(project_id=test_project.id)
    # Ensure the state reflects that project is selected and ready for a task
    engine_instance.current_project_state.current_status = EngineState.PROJECT_SELECTED.name 
    engine_instance.state = EngineState.PROJECT_SELECTED
    
    print(f"Engine current project set to: {engine_instance.current_project.name}")
    print(f"Engine initial state: {engine_instance.state.name}")
    print(f"Engine project state initial history: {engine_instance.current_project_state.conversation_history}")

    # Call start_task
    task_instruction = "Start test task for context check: please list the main files you see."
    print(f"Calling start_task with instruction: '{task_instruction}'")
    engine_instance.start_task(initial_user_instruction=task_instruction)

    print("--- Autonomous Context Test Finished ---")
    print("Inspect the DEBUG_ENGINE and FINAL PROMPT TO GEMINI prints above.")

    # Cleanup (optional, but good practice for temp files)
    # print(f"Cleaning up project: {test_project_id}")
    # delete_project_by_id(test_project_id) # If you implement this in persistence
    # shutil.rmtree(TEMP_WORKSPACE_NAME) # Be careful with this!

if __name__ == "__main__":
    # Ensure config.ini exists for ConfigManager and GeminiComms initialization
    if not os.path.exists("config.ini"):
        print("ERROR: config.ini not found. Please ensure it exists in the root directory.")
        # Create a dummy one if absolutely necessary for the test to run minimally
        try:
            with open("config.ini", "w") as f:
                f.write("[API_KEYS]\nGEMINI_API_KEY = YOUR_API_KEY_HERE\n")
                f.write("[SETTINGS]\nGEMINI_MODEL = gemini-1.5-flash-latest\n") # Use a known fast model
                f.write("[DIRECTORIES]\nDEV_LOGS_DIR = dev_logs\n")
                f.write("DEV_INSTRUCTIONS_DIR = dev_instructions\n")
                f.write("[ENGINE_CONFIG]\nMAX_HISTORY_TURNS = 10\n")
                f.write("MAX_CONTEXT_TOKENS = 30000\n")
                f.write("CURSOR_LOG_TIMEOUT_SECONDS = 300\n")
            print("Created a dummy config.ini. PLEASE REPLACE API KEY if you want live calls.")
        except Exception as e:
            print(f"Failed to create dummy config.ini: {e}")
            sys.exit(1)
            
    run_test() 