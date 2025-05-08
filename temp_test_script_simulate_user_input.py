import os
import shutil
import time
from engine import OrchestrationEngine, EngineState # Corrected import
from models import Project, Turn, ProjectState # Corrected import
from config_manager import ConfigManager # Corrected import
# from persistence import add_project_config_to_gui_and_engine, remove_project_config_from_gui_and_engine, save_project_specific_config # Corrected import - REMOVING UNUSED IMPORT

# Ensure Orchestrator Prime modules can be found if script is in root
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))


def run_test():
    print("--- Starting Test: Simulate User Input ---")
    try:
        # 0. Initial Setup
        # Ensure app_data exists for ConfigManager, though engine does this too
        if not os.path.exists("app_data"):
            os.makedirs("app_data")
        
        # Create a ConfigManager instance (needed by engine)
        config_manager = ConfigManager()

        # 1. Initialize Engine
        engine = OrchestrationEngine()
        print(f"Engine initialized. Initial state: {engine.get_current_state()}")

        # 2. Setup Dummy Project
        dummy_project_name = "test_user_input_project"
        dummy_project_path = os.path.abspath(dummy_project_name) # Engine expects absolute path for workspace
        dummy_project_goal = "Test goal for user input simulation"
        
        # Create project directory if it doesn't exist
        if not os.path.exists(dummy_project_path):
            os.makedirs(dummy_project_path)
            print(f"Created dummy project directory: {dummy_project_path}")

        # Create a dummy project object
        project = Project(name=dummy_project_name, workspace_root_path=dummy_project_path, overall_goal=dummy_project_goal)
        print(f"Created Project object: {project}")
        
        # Add project to persistence (and engine's internal list)
        # For this test, we directly manipulate the engine's active project
        # as if it was selected through the GUI.
        engine.current_project = project
        # engine.current_project_state = {"name": project.name, "path": project.path, "goal": project.goal, "conversation_history": []} # This needs to be a ProjectState object
        # For now, let the engine internally handle ProjectState creation or retrieval if possible,
        # or create a minimal one. The set_active_project method usually does this.
        # Since we are bypassing it, we need to set a compatible ProjectState.
        # A minimal ProjectState requires a project_id. Let's use the project name for simplicity in this test.
        engine.current_project_state = ProjectState(project_id=project.name) # Minimal ProjectState
        # Add a turn to conversation_history to mimic state before user input
        engine.current_project_state.conversation_history.append(
            Turn(sender="GEMINI_CLARIFICATION_REQUEST", message="Please provide details.")
        )
        print(f"Set engine.current_project_state to a minimal ProjectState object for project: {project.name}")
        
        # Ensure dev_instructions and dev_logs directories exist for this project
        dev_instructions_dir = os.path.join(dummy_project_path, "dev_instructions")
        dev_logs_dir = os.path.join(dummy_project_path, "dev_logs")
        os.makedirs(dev_instructions_dir, exist_ok=True)
        os.makedirs(dev_logs_dir, exist_ok=True)
        print(f"Ensured dev_instructions and dev_logs exist for {dummy_project_name}")

        print(f"Set active project in engine: {engine.current_project.name if engine.current_project else 'None'}")

        # 3. Manually set engine state
        engine.current_state = EngineState.PAUSED_WAITING_USER_INPUT
        # Add a placeholder "GEMINI_CLARIFICATION_REQUEST" to history as the engine expects it before user input
        # engine.current_project_state["conversation_history"].append(
        #     Turn(sender="GEMINI_CLARIFICATION_REQUEST", message="Please provide details.", timestamp=engine._get_timestamp())
        # )
        print(f"Engine state manually set to: {engine.get_current_state()}")

        # 4. Formulate Simulated User Response
        simulated_response = "Okay, here is a simplified project structure: project_folder/\\n  main.py\\n  utils/\\n    __init__.py\\n    helper.py\\n  data/\\n    input.csv"
        print(f"Simulated user response: '{simulated_response}'")

        # 5. Call resume_with_user_input
        print("Calling engine.resume_with_user_input()...")
        engine.resume_with_user_input(simulated_response)
        
        # Allow some time for async operations within the engine if any (e.g., thread for Gemini call)
        # The modified gemini_comms should be synchronous, but good practice for future.
        # For this specific test with mocked comms, immediate check should be fine.
        time.sleep(0.5) # Brief pause for file I/O and state change propagation

        # 6. Observe Outcome
        final_engine_state = engine.get_current_state()
        print(f"Engine state after resume_with_user_input: {final_engine_state}")

        expected_next_state = EngineState.RUNNING_WAITING_LOG
        if final_engine_state == expected_next_state:
            print(f"SUCCESS: Engine transitioned to {expected_next_state} as expected.")
        else:
            print(f"FAILURE: Engine transitioned to {final_engine_state}, expected {expected_next_state}.")

        next_step_file_path = os.path.join(dummy_project_path, "dev_instructions", "next_step.txt")
        expected_next_step_content = "Okay, I see the project structure. Now, please create a file named \'test_file.py\' in the root and write \'print(\\'Hello World\\')\' into it."
        
        if os.path.exists(next_step_file_path):
            with open(next_step_file_path, 'r') as f:
                actual_content = f.read()
            print(f"Found next_step.txt. Content: '{actual_content}'")
            if actual_content == expected_next_step_content:
                print("SUCCESS: next_step.txt content is as expected.")
            else:
                print(f"FAILURE: next_step.txt content mismatch. Expected: '{expected_next_step_content}'")
        else:
            print(f"FAILURE: next_step.txt not found at {next_step_file_path}.")

        # 7. Cleanup
        print("--- Test Cleanup ---")
        if engine:
            engine.shutdown() # Gracefully shutdown engine threads
            print("Engine shutdown.")
        
        if os.path.exists(dummy_project_path):
            try:
                shutil.rmtree(dummy_project_path)
                print(f"Removed dummy project directory: {dummy_project_path}")
            except Exception as e_cleanup:
                print(f"Error removing dummy project directory {dummy_project_path}: {e_cleanup}")
        
        print("--- Test Finished ---")
    except Exception as e_main_test:
        print(f"CRITICAL ERROR IN TEST SCRIPT: {type(e_main_test).__name__} - {e_main_test}")
        import traceback
        traceback.print_exc()
        # Ensure cleanup still attempts if an error occurred mid-test
        # This is a simplified version of cleanup, assuming 'engine' and 'dummy_project_path' might exist
        print("Attempting emergency cleanup after error...")
        if 'engine' in locals() and engine:
            try:
                engine.shutdown()
                print("Emergency engine shutdown completed.")
            except Exception as e_shutdown_emergency:
                print(f"Emergency engine shutdown failed: {e_shutdown_emergency}")
        if 'dummy_project_path' in locals() and os.path.exists(dummy_project_path):
            try:
                shutil.rmtree(dummy_project_path)
                print(f"Emergency removal of dummy project directory: {dummy_project_path} completed.")
            except Exception as e_rm_emergency:
                print(f"Emergency removal of dummy project directory {dummy_project_path} failed: {e_rm_emergency}")

if __name__ == "__main__":
    run_test() 