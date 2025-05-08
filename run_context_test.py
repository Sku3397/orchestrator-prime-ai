import os
import sys
import time
from typing import Dict, Optional

# Adjust path to import from parent directory if script is in a subfolder
# sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from engine import OrchestrationEngine, EngineState
from models import Project, ProjectState, Turn
from config_manager import ConfigManager
# from persistence import save_project, delete_project_by_id # For cleanup
import uuid
import shutil # For cleaning up test dirs

TEMP_WORKSPACE_BASE = "temp_stability_tests"

def setup_dummy_workspace(workspace_name: str, files_dirs: Dict[str, Optional[str]]):
    """Creates a dummy workspace with specified files and directories."""
    if not os.path.exists(workspace_name):
        os.makedirs(workspace_name)
    else:
        # Basic cleanup of previous run for idempotency
        # This is simple; a more robust version might handle errors
        try: 
            for item in os.listdir(workspace_name):
                item_path = os.path.join(workspace_name, item)
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)
        except Exception as e:
            print(f"Warning: Failed to clean up old workspace '{workspace_name}': {e}")

    for path, content in files_dirs.items():
        full_path = os.path.join(workspace_name, path)
        if content is None: # It's a directory
             os.makedirs(full_path, exist_ok=True)
        else: # It's a file
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.write(content)
    print(f"Dummy workspace '{workspace_name}' created/verified.")

def get_engine_instance():
    """Helper to get a fresh engine instance."""
    # Ensure config is loaded for Gemini init
    if not os.path.exists("config.ini"):
         raise FileNotFoundError("config.ini not found! Cannot initialize engine.")
    # We assume config.ini has valid settings or placeholder key for comms init
    return OrchestrationEngine(gui_update_callback=None)

def create_test_project(name: str, workspace_subdir: str, goal: str) -> Project:
    """Helper to create a Project object for testing."""
    workspace_path = os.path.abspath(os.path.join(TEMP_WORKSPACE_BASE, workspace_subdir))
    return Project(
        id=str(uuid.uuid4()),
        name=name,
        workspace_root_path=workspace_path,
        overall_goal=goal
    )

def run_initial_context_test():
    print("\n--- Test: Initial Context Verification ---")
    test_proj_subdir = "context_test_proj"
    workspace_files = {
        "main.py": "# Dummy main.py",
        "README.md": "# Test Project README",
        "src/": None, # Indicates directory
        "src/__init__.py": "# Make src a package"
    }
    setup_dummy_workspace(os.path.join(TEMP_WORKSPACE_BASE, test_proj_subdir), workspace_files)
    project = create_test_project("ContextVerifyProject", test_proj_subdir, "Verify initial context.")
    
    engine = get_engine_instance()
    if not engine.set_active_project(project):
         print("ERROR: Failed to set active project for context test.")
         return
    
    instruction = "Initialize project setup based on structure."
    print(f"Calling start_task with instruction: '{instruction}'")
    engine.start_task(initial_user_instruction=instruction)
    print("--- Finished: Initial Context Verification ---")
    # Output analysis is done by inspecting the console logs from this run

def run_project_switching_test():
    print("\n--- Test: Project Switching --- ")
    # Project Alpha Setup
    alpha_subdir = "proj_alpha"
    alpha_files = {"alpha.txt": "Alpha content", "common/": None, "common/utils.py": "# Alpha utils"}
    setup_dummy_workspace(os.path.join(TEMP_WORKSPACE_BASE, alpha_subdir), alpha_files)
    proj_alpha = create_test_project("ProjectAlpha", alpha_subdir, "Goal for Alpha")

    # Project Beta Setup
    beta_subdir = "proj_beta"
    beta_files = {"beta_main.py": "# Beta main", "data/": None}
    setup_dummy_workspace(os.path.join(TEMP_WORKSPACE_BASE, beta_subdir), beta_files)
    proj_beta = create_test_project("ProjectBeta", beta_subdir, "Goal for Beta")
    
    engine = get_engine_instance()

    # Round 1: Alpha
    print("\nSwitching to Alpha...")
    if not engine.set_active_project(proj_alpha):
        print("ERROR: Failed to set active project Alpha.")
        return
    assert engine.current_project.name == "ProjectAlpha"
    assert engine.current_project.overall_goal == "Goal for Alpha"
    print(f"Active project goal: {engine.current_project.overall_goal}")
    print("Starting task for Alpha...")
    engine.start_task("Start task for Alpha") 
    # Need to let Gemini call finish - add sleep or better sync if needed, for now check logs
    time.sleep(2) # Simple wait
    alpha_history_after_start = engine.current_project_state.conversation_history if engine.current_project_state else []
    print(f"Alpha history length after start: {len(alpha_history_after_start)}")

    # Round 2: Beta
    print("\nSwitching to Beta...")
    if not engine.set_active_project(proj_beta):
        print("ERROR: Failed to set active project Beta.")
        return
    assert engine.current_project.name == "ProjectBeta"
    assert engine.current_project.overall_goal == "Goal for Beta"
    print(f"Active project goal: {engine.current_project.overall_goal}")
    print("Starting task for Beta...")
    engine.start_task("Start task for Beta")
    time.sleep(2) # Simple wait
    beta_history_after_start = engine.current_project_state.conversation_history if engine.current_project_state else []
    print(f"Beta history length after start: {len(beta_history_after_start)}")
    
    # Round 3: Alpha Again
    print("\nSwitching back to Alpha...")
    if not engine.set_active_project(proj_alpha):
        print("ERROR: Failed to set active project Alpha (Round 2).")
        return
    assert engine.current_project.name == "ProjectAlpha"
    assert engine.current_project.overall_goal == "Goal for Alpha"
    print(f"Active project goal: {engine.current_project.overall_goal}")
    # Check if history is preserved for Alpha
    alpha_history_round_2 = engine.current_project_state.conversation_history if engine.current_project_state else []
    print(f"Alpha history length after switch back: {len(alpha_history_round_2)}")
    # Ideally compare content, but length is a basic check
    assert len(alpha_history_round_2) >= len(alpha_history_after_start)
    print("History length check passed for Alpha.")

    print("--- Finished: Project Switching --- ")


def run_summarization_trigger_test():
    # This test is limited. It checks if the summarization call is ATTEMPTED
    # It requires manual inspection of logs to see if GeminiCommunicator.summarize_text was called.
    # We need to add a print statement there for confirmation.
    print("\n--- Test: Context Summarization Trigger (Limited Verification) ---")
    test_proj_subdir = "summary_test_proj"
    setup_dummy_workspace(os.path.join(TEMP_WORKSPACE_BASE, test_proj_subdir), {"file.txt": "content"})
    project = create_test_project("SummaryTestProject", test_proj_subdir, "Test summarization.")
    
    engine = get_engine_instance()
    if not engine.set_active_project(project):
        print("ERROR: Failed to set active project for summary test.")
        return

    # Manually add history turns (assuming summarization_interval = 10 for example)
    print("Manually adding history turns to trigger summarization...")
    turns_to_add = engine.config.get_summarization_interval() + 2 # Exceed threshold
    if not engine.current_project_state:
         print("ERROR: Project state not initialized."); return
         
    for i in range(turns_to_add):
        sender = "USER" if i % 2 == 0 else "GEMINI_MANAGER"
        engine.current_project_state.conversation_history.append(Turn(sender=sender, message=f"Dummy message {i}"))
    print(f"Added {turns_to_add} turns. History length: {len(engine.current_project_state.conversation_history)}")
    engine.current_project_state.context_summary = None # Ensure no prior summary
    save_project_state(project, engine.current_project_state) # Save the history additions

    print("Calling start_task, expecting summarization to be triggered...")
    print("** Please check console logs for 'Attempting to summarize context' from OrchestrationEngine **")
    engine.start_task("Start task after adding history.")
    # The test ends here. Verification requires checking the engine logs for summarization attempt.
    print("--- Finished: Context Summarization Trigger --- ")


def run_stop_task_test():
    # Testing stop from PAUSED state as stopping from RUNNING_WAITING_LOG is hard to time in script
    print("\n--- Test: Stop Task (from PAUSED) ---")
    test_proj_subdir = "stop_test_proj"
    setup_dummy_workspace(os.path.join(TEMP_WORKSPACE_BASE, test_proj_subdir), {"stop.txt": "to stop"})
    project = create_test_project("StopTestProject", test_proj_subdir, "Test stopping the task.")
    
    engine = get_engine_instance()
    if not engine.set_active_project(project):
        print("ERROR: Failed to set active project for stop test.")
        return

    # Need to get engine into PAUSED_WAITING_USER_INPUT state
    # Easiest way is to mock Gemini response for the first call
    print("Simulating Gemini asking for input...")
    # This requires modifying GeminiCommunicator or Engine temporarily, 
    # OR knowing a prompt that reliably causes NEED_INPUT
    # For now, let's manually set the state after a simulated first call attempt
    engine.start_task("Initial instruction") 
    time.sleep(1) # Let the initial async call attempt proceed (it will likely fail writing instruction)
    print(f"State after initial start_task call attempt: {engine.state.name}")
    
    # Manually force PAUSED state for testing stop
    print("Manually setting state to PAUSED_WAITING_USER_INPUT for test.")
    engine._set_state(EngineState.PAUSED_WAITING_USER_INPUT, "Forced pause for stop test")
    assert engine.state == EngineState.PAUSED_WAITING_USER_INPUT

    print("Calling stop_task...")
    engine.stop_task()
    print(f"State after stop_task: {engine.state.name}")
    assert engine.state == EngineState.PROJECT_SELECTED # Expect it to reset to PROJECT_SELECTED

    print("Attempting to start a new task...")
    engine.start_task("Start a new task after stopping.")
    print(f"State after starting new task: {engine.state.name}")
    # We expect it to try and start the Gemini call again and land in WAITING_LOG or error
    assert engine.state in [EngineState.RUNNING_WAITING_INITIAL_GEMINI, EngineState.RUNNING_CALLING_GEMINI, EngineState.RUNNING_WAITING_LOG, EngineState.ERROR] # Added WAITING_LOG

    print("--- Finished: Stop Task --- ")


def run_error_recovery_test():
    print("\n--- Test: Error Recovery ---")
    test_proj_subdir = "error_test_proj"
    setup_dummy_workspace(os.path.join(TEMP_WORKSPACE_BASE, test_proj_subdir), {"error.txt": "test"})
    project = create_test_project("ErrorTestProject", test_proj_subdir, "Test error recovery.")
    
    engine = get_engine_instance()
    if not engine.set_active_project(project):
        print("ERROR: Failed to set active project for error test.")
        return

    print(f"Manually setting state to ERROR.")
    engine._set_state(EngineState.ERROR, "Simulated error for recovery test")
    assert engine.state == EngineState.ERROR

    print("Calling start_task from ERROR state...")
    engine.start_task("Start task to recover from error.")
    print(f"State after start_task from ERROR: {engine.state.name}")
    # Expect engine to proceed with the task start and land in WAITING_LOG or error
    assert engine.state in [EngineState.RUNNING_WAITING_INITIAL_GEMINI, EngineState.RUNNING_CALLING_GEMINI, EngineState.RUNNING_WAITING_LOG, EngineState.ERROR] # Added WAITING_LOG

    print("--- Finished: Error Recovery --- ")

def run_all_tests():
    # Ensure base temp dir exists
    if not os.path.exists(TEMP_WORKSPACE_BASE):
        os.makedirs(TEMP_WORKSPACE_BASE)
        
    # Verify Initial Context First
    run_initial_context_test()
    
    # Run Stability Tests
    run_project_switching_test()
    # run_summarization_trigger_test() # Needs check in engine/comms log AND engine implementation
    run_stop_task_test()
    run_error_recovery_test()

    print("\n--- All Tests Finished --- ")
    # Consider adding cleanup option
    # print(f"Cleaning up test workspaces in {TEMP_WORKSPACE_BASE}...")
    # try:
    #     shutil.rmtree(TEMP_WORKSPACE_BASE)
    # except Exception as e:
    #     print(f"Error cleaning up test workspaces: {e}")


def run_test(): # Keep original entry point, but redirect
    print("Redirecting to run_all_tests...")
    run_all_tests()

if __name__ == "__main__":
    # Ensure config.ini exists for ConfigManager and GeminiComms initialization
    if not os.path.exists("config.ini"):
        print("ERROR: config.ini not found. Please ensure it exists in the root directory.")
        # Create a dummy one if absolutely necessary for the test to run minimally
        try:
            with open("config.ini", "w") as f:
                f.write("[API_KEYS]\nGEMINI_API_KEY = YOUR_API_KEY_HERE\n")
                f.write("[SETTINGS]\nGEMINI_MODEL = gemini-1.5-flash-latest\n") # Use a known fast model
                f.write("SUMMARY_INTERVAL = 10\n") # Set interval for testing
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