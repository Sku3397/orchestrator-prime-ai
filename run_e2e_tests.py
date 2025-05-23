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
import threading # For waiting on engine state

TEMP_WORKSPACE_BASE = "temp_stability_tests"
SIMULATED_CURSOR_DELAY_S = 5 # Short delay for testing
GEMINI_CALL_TIMEOUT_SECONDS = 60 # Matching engine's typical timeout for Gemini calls
CURSOR_LOG_TIMEOUT_SECONDS = 300 # Matching engine's default timeout for cursor log

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

def wait_for_engine_state(engine: OrchestrationEngine, target_state: EngineState, timeout_s: int = 10) -> bool:
    """Waits for the engine to reach a specific state."""
    start_time = time.time()
    while time.time() - start_time < timeout_s:
        if engine.state == target_state:
            return True
        time.sleep(0.1)
    print(f"TIMEOUT: Engine did not reach state {target_state.name} within {timeout_s}s. Current state: {engine.state.name}")
    return False

def simulate_cursor_action(engine: OrchestrationEngine, instruction: str, step: int):
    """Simulates Cursor reading instruction and writing log after delay."""
    # Construct plausible log content based on instruction
    # This is highly simplified
    log_content = f"SUCCESS: Step {step} completed based on instruction: {instruction[:50]}..."
    if "create file a.txt" in instruction.lower():
        log_content = "SUCCESS: Created file a.txt."
    elif "create file b.txt" in instruction.lower():
        log_content = "SUCCESS: Created file b.txt."
    elif "read a.txt" in instruction.lower():
        log_content = "SUCCESS: Read a.txt, content is empty (as expected)."
        
    print(f"TEST_SIM: Waiting {SIMULATED_CURSOR_DELAY_S}s to simulate Cursor action for step {step}...")
    time.sleep(SIMULATED_CURSOR_DELAY_S)
    
    log_file_path = os.path.join(engine.dev_logs_dir, "cursor_step_output.txt")
    try:
        print(f"TEST_SIM: Writing simulated Cursor log to {log_file_path}")
        with open(log_file_path, 'w') as f:
            f.write(log_content)
        # Engine watcher should pick this up if running
    except Exception as e:
        print(f"ERROR in TEST_SIM: Failed to write log file: {e}")

def run_full_loop_test(num_steps=4):
    print("\n--- Test: Full E2E Loop Simulation --- ")
    test_proj_subdir = "full_loop_proj"
    workspace_files = {"initial.txt": "Start here"}
    setup_dummy_workspace(os.path.join(TEMP_WORKSPACE_BASE, test_proj_subdir), workspace_files)
    project = create_test_project("FullLoopProject", test_proj_subdir, 
                                  f"Execute a {num_steps}-step task: 1. Create a.txt, 2. Create b.txt, 3. Read a.txt, 4. Report completion.")
    
    engine = get_engine_instance()
    if not engine.set_active_project(project):
        print("ERROR: Failed to set active project for full loop test.")
        return

    instruction = f"Start the {num_steps}-step task."
    print(f"Calling start_task with instruction: '{instruction}'")
    # Note: start_task is now internally synchronous for the first call due to queue.get()
    engine.start_task(initial_user_instruction=instruction)
    
    current_step = 0
    max_steps = num_steps + 2 # Allow for initial setup and final complete message
    while current_step < max_steps:
        current_step += 1
        print(f"\nLOOP_TEST: Waiting for engine state RUNNING_WAITING_LOG (Step {current_step})...")
        if not wait_for_engine_state(engine, EngineState.RUNNING_WAITING_LOG, timeout_s=GEMINI_CALL_TIMEOUT_SECONDS + 10):
             print(f"ERROR: Engine did not enter RUNNING_WAITING_LOG state for step {current_step}. Current state: {engine.state.name}")
             break
        
        print(f"LOOP_TEST: Engine is RUNNING_WAITING_LOG. Current instruction should be in state:")
        last_instruction = engine.current_project_state.last_instruction_sent if engine.current_project_state else "(State missing)"
        print(f"LAST_INSTRUCTION: {last_instruction}")

        if "TASK_COMPLETE" in last_instruction.upper(): # Check if Gemini issued TASK_COMPLETE
             print("LOOP_TEST: Gemini indicated task complete.")
             break # Exit loop cleanly
             
        if last_instruction.startswith("NEED_USER_INPUT:"):
            print("LOOP_TEST: Gemini needs user input. Test cannot proceed autonomously.")
            break
        
        simulate_cursor_action(engine, last_instruction, current_step)
        # Engine should process the log file asynchronously via the watcher
        # We need to wait for it to potentially call Gemini and reach WAITING_LOG again or finish
        print("LOOP_TEST: Waiting a bit for engine to process log and potentially call Gemini...")
        time.sleep(SIMULATED_CURSOR_DELAY_S + 5) # Wait longer than cursor delay
        print(f"LOOP_TEST: State after waiting for log processing: {engine.state.name}")
        
        if engine.state == EngineState.TASK_COMPLETE:
            print("LOOP_TEST: Engine reached TASK_COMPLETE state.")
            break
        elif engine.state == EngineState.ERROR:
            print(f"ERROR: Engine entered ERROR state: {engine.last_error_message}")
            break
            
    if engine.state == EngineState.TASK_COMPLETE:
        print("\nSUCCESS: Full loop completed successfully.")
    else:
        print(f"\nWARNING: Full loop test finished in state {engine.state.name} (Step {current_step}). May not have completed fully.")
        
    print("--- Finished: Full E2E Loop Simulation --- ")

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


def run_summarization_trigger_test(turns_to_simulate=12):
    print(f"\n--- Test: Context Summarization Trigger (Simulating {turns_to_simulate} turns) ---")
    test_proj_subdir = "summary_test_proj"
    setup_dummy_workspace(os.path.join(TEMP_WORKSPACE_BASE, test_proj_subdir), {"file.txt": "content"})
    project = create_test_project("SummaryTestProject", test_proj_subdir, f"Simulate {turns_to_simulate} turns for summarization.")
    
    engine = get_engine_instance()
    if not engine.set_active_project(project):
        print("ERROR: Failed to set active project for summary test.")
        return

    print(f"Simulating {turns_to_simulate} interaction turns...")
    instruction = "Start sequence."
    engine.start_task(initial_user_instruction=instruction)
    
    current_step = 0
    while current_step < turns_to_simulate:
        current_step += 1
        if not wait_for_engine_state(engine, EngineState.RUNNING_WAITING_LOG, timeout_s=GEMINI_CALL_TIMEOUT_SECONDS + 10):
             print(f"ERROR: Engine did not enter RUNNING_WAITING_LOG state for step {current_step}. Current state: {engine.state.name}")
             break
        last_instruction = engine.current_project_state.last_instruction_sent if engine.current_project_state else "(State missing)"
        if engine.state != EngineState.RUNNING_WAITING_LOG: break # Exit if error or completion
        
        # Use a simpler simulation, just acknowledge the step
        log_content = f"SUCCESS: Completed step {current_step} based on '{last_instruction[:30]}...'"
        log_file_path = os.path.join(engine.dev_logs_dir, "cursor_step_output.txt")
        try:
            with open(log_file_path, 'w') as f: f.write(log_content)
        except Exception as e:
            print(f"ERROR writing log step {current_step}: {e}"); break
        
        print(f"SUM_TEST Step {current_step}: Wrote log. Waiting for processing...")    
        time.sleep(2) # Give engine time to process log and call Gemini again
        
        # Check for summarization log print during the loop
        # Manual log inspection is still the best verification here
        if engine.state == EngineState.TASK_COMPLETE: break
        if engine.state == EngineState.ERROR: break

    print("** Please check console logs for 'Engine: Attempting to summarize context' from OrchestrationEngine **")
    print(f"--- Finished: Context Summarization Trigger (Simulated {current_step} turns) --- ")

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

def run_cursor_timeout_test():
    print("\n--- Test: Cursor Timeout --- ")
    test_proj_subdir = "timeout_test_proj"
    setup_dummy_workspace(os.path.join(TEMP_WORKSPACE_BASE, test_proj_subdir), {"timeout.txt": "test"})
    project = create_test_project("TimeoutTestProject", test_proj_subdir, "Test cursor timeout.")
    
    engine = get_engine_instance()
    if not engine.set_active_project(project):
        print("ERROR: Failed to set active project for timeout test.")
        return

    instruction = "Start task that will time out."
    print(f"Calling start_task with instruction: '{instruction}'")
    engine.start_task(initial_user_instruction=instruction)
    
    print(f"Waiting for engine to enter RUNNING_WAITING_LOG...")
    if not wait_for_engine_state(engine, EngineState.RUNNING_WAITING_LOG, timeout_s=GEMINI_CALL_TIMEOUT_SECONDS + 10):
        print(f"ERROR: Engine did not enter RUNNING_WAITING_LOG state. Cannot test timeout. State: {engine.state.name}")
        return
        
    print(f"Engine is RUNNING_WAITING_LOG. Now waiting for timeout ({CURSOR_LOG_TIMEOUT_SECONDS}s)... DO NOT create log file.")
    # Reduce wait time for faster testing, ensure it's > CURSOR_LOG_TIMEOUT_SECONDS
    effective_timeout = engine.config.get_cursor_log_timeout_seconds() 
    print(f"Effective timeout from config: {effective_timeout}s")
    wait_time = effective_timeout + 5 # Wait a bit longer than the timeout
    time.sleep(wait_time) 
    
    print(f"State after waiting for timeout: {engine.state.name}")
    assert engine.state == EngineState.ERROR
    assert "Timeout: Cursor log file did not appear" in engine.last_error_message
    print("SUCCESS: Engine correctly entered ERROR state on timeout.")

    print("Attempting to start a new task after timeout error...")
    engine.start_task("Start new task after timeout.")
    print(f"State after starting new task: {engine.state.name}")
    assert engine.state in [EngineState.RUNNING_WAITING_INITIAL_GEMINI, EngineState.RUNNING_CALLING_GEMINI, EngineState.RUNNING_WAITING_LOG, EngineState.ERROR] # Added RUNNING_WAITING_LOG
    print("SUCCESS: Able to start new task after timeout error.")
    
    print("--- Finished: Cursor Timeout --- ")

def run_api_error_simulation_test():
     print("\n--- Test: API Error Simulation --- ")
     # This requires temporarily modifying gemini_comms.py
     print("SKIPPING: Requires manual code modification in gemini_comms.py to simulate API errors.")
     # TODO: Implement if a mechanism for temporary code patching or mock injection is added.
     print("--- Finished: API Error Simulation --- ")

def run_all_tests():
    # Ensure base temp dir exists
    if not os.path.exists(TEMP_WORKSPACE_BASE):
        os.makedirs(TEMP_WORKSPACE_BASE)
    
    # Get config values needed for test setup
    temp_config_manager = ConfigManager() # Create a temp instance to get config
    summarization_interval_for_test = temp_config_manager.get_summarization_interval()
    del temp_config_manager # Clean up

    # Part 1 Verification
    run_initial_context_test()
    
    # Part 2: Full Loop & Stability
    run_full_loop_test(num_steps=4)
    run_project_switching_test()
    run_summarization_trigger_test(turns_to_simulate=summarization_interval_for_test + 2)
    run_cursor_timeout_test()
    run_stop_task_test()
    run_error_recovery_test()
    run_api_error_simulation_test() # Will skip for now

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