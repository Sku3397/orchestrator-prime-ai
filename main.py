import os
import sys
import traceback
import logging # Added
from engine import OrchestrationEngine
from persistence import load_projects, add_project, Project
from models import OrchestratorState # For checking engine state

# --- Setup Logging ---
# Create a logger
logger = logging.getLogger("orchestrator_prime")
logger.setLevel(logging.DEBUG) # Set the minimum level for the logger

# Create file handler which logs even debug messages
fh = logging.FileHandler("orchestrator_prime.log", mode='w') # Overwrite log file each run
fh.setLevel(logging.DEBUG)

# Create console handler with a higher log level
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)

# Create formatter and add it to the handlers
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s')
fh.setFormatter(formatter)
ch.setFormatter(formatter)

# Add the handlers to the logger
logger.addHandler(fh)
logger.addHandler(ch)
# --- End Logging Setup ---

# It's good practice for persistence layer to handle its own directory/file creation.
# The initial check in main.py can be a fallback or removed if persistence is robust.
# For now, let's keep it to ensure app_data exists for ConfigManager or other early needs
# if they don't trigger persistence.load_projects() first.

def ensure_app_data_scaffolding():
    """Ensures basic app_data directory and an empty projects.json exists."""
    app_data_dir = "app_data"
    projects_json_path = os.path.join(app_data_dir, "projects.json")
    try:
        if not os.path.exists(app_data_dir):
            os.makedirs(app_data_dir)
            logger.info(f"Created directory: {app_data_dir}")
        
        if not os.path.exists(projects_json_path):
            with open(projects_json_path, 'w') as f:
                f.write("[]") # Initialize with an empty JSON array
            logger.info(f"Created empty projects file: {projects_json_path}")

    except OSError as e:
        logger.critical(f"CRITICAL ERROR: Could not create app_data directory or projects.json: {e}")
        sys.exit(1)

def print_welcome():
    print("\\nWelcome to Orchestrator Prime (Terminal Edition)")
    print("-------------------------------------------------")
    print("Type 'help' for a list of commands.")

def print_help():
    print("\\nAvailable Commands:")
    print("  project list                - List all available projects.")
    print("  project add                 - Add a new project.")
    print("  project select <name>       - Select an active project.")
    print("  goal <initial goal text>    - Set the initial goal for the selected project and start.")
    print("  input <response text>       - Provide input when Gemini is waiting.")
    print("  status                      - Display the current engine status and active project.")
    print("  stop                        - Stop the current task gracefully.")
    print("  quit                        - Shutdown Orchestrator Prime and exit.")
    print("  help                        - Show this help message.")
    print("\\nAny other input will be treated as a new goal/instruction for the active project if one is selected.")

def run_terminal_interface(engine: OrchestrationEngine):
    print_welcome()
    active_project_name = None

    while True:
        prompt_project_name = f" (Project: {active_project_name})" if active_project_name else ""
        prompt = f"OP{prompt_project_name} > "
        
        try:
            user_input = input(prompt).strip()

            if not user_input:
                continue

            # Handle engine waiting for user input first
            if engine.state == OrchestratorState.PAUSED_WAITING_USER_INPUT:
                if user_input.lower().startswith("input "):
                    response = user_input[len("input "):].strip()
                    if response:
                        print(f"--- Resuming with your input: '{response}' ---")
                        engine.resume_with_user_input(response)
                    else:
                        print("--- Input cannot be empty. Please provide a response. ---")
                else:
                    # If engine is waiting, but user types something other than "input ...",
                    # inform them and continue waiting.
                    print(f"--- Engine is waiting for input. Use 'input <your response>' or see 'help'. ---")
                    if hasattr(engine, 'pending_user_question') and engine.pending_user_question:
                         print(f"Gemini's Question: {engine.pending_user_question}")
                    continue # Continue to next iteration to get 'input ...' command

            elif user_input.lower() == "quit":
                print("--- Shutting down Orchestrator Prime... ---")
                engine.shutdown()
                break
            
            elif user_input.lower() == "help":
                print_help()

            elif user_input.lower() == "project list":
                projects = load_projects()
                if projects:
                    print("--- Available Projects: ---")
                    for proj in projects:
                        print(f"  - {proj.name}")
                else:
                    print("--- No projects found. Use 'project add' to create one. ---")

            elif user_input.lower() == "project add":
                print("--- Adding a new project ---")
                name = input("Project Name: ").strip()
                while not name:
                    name = input("Project Name (cannot be empty): ").strip()
                
                root_path = input("Workspace Root Path: ").strip()
                while not os.path.isdir(root_path):
                    root_path = input("Invalid path. Workspace Root Path (must be an existing directory): ").strip()
                
                goal = input("Overall Goal for the project: ").strip()
                while not goal:
                    goal = input("Overall Goal (cannot be empty): ").strip()

                new_project = Project(name=name, workspace_root_path=root_path, overall_goal=goal)
                try:
                    add_project(new_project)
                    print(f"--- Project '{name}' added successfully. ---")
                except Exception as e:
                    print(f"--- Error adding project: {e} ---")
            
            elif user_input.lower().startswith("project select "):
                name_to_select = user_input[len("project select "):].strip()
                if name_to_select:
                    try:
                        if engine.set_active_project(name_to_select):
                            active_project_name = name_to_select
                            print(f"--- Project '{active_project_name}' selected. ---")
                        else:
                            print(f"--- Could not select project '{name_to_select}'. It might not exist or failed to load. ---")
                    except Exception as e:
                        print(f"--- Error selecting project '{name_to_select}': {e} ---")
                else:
                    print("--- Please specify a project name to select. Usage: project select <name> ---")

            elif user_input.lower() == "status":
                print(f"--- Engine Status: {engine.state.value} ---")
                if engine.active_project:
                    print(f"--- Active Project: {engine.active_project.name} ---")
                    print(f"    Overall Goal: {engine.active_project.overall_goal}")
                    if engine.current_project_state and engine.current_project_state.current_task_goal:
                         print(f"    Current Task Goal: {engine.current_project_state.current_task_goal}")
                    
                    if engine.state.name.startswith("RUNNING_") and \
                       engine.current_project_state and \
                       engine.current_project_state.last_instruction_sent:
                        # Display last instruction sent to cursor if available and not too long
                        last_instr = engine.current_project_state.last_instruction_sent
                        display_instr = (last_instr[:100] + '...') if len(last_instr) > 100 else last_instr
                        print(f"    Last Instruction to Cursor: {display_instr}")

                else:
                    print("--- No active project selected. ---")

                if engine.state == OrchestratorState.PAUSED_WAITING_USER_INPUT:
                    if hasattr(engine, 'pending_user_question') and engine.pending_user_question:
                        print(f"    Pending Question from Gemini: {engine.pending_user_question}")
                    else:
                        print("    Gemini is waiting for input (no specific question stored).")
                
                if engine.state.name.startswith("ERROR") or engine.state == OrchestratorState.ERROR:
                    if hasattr(engine, 'last_error_message') and engine.last_error_message:
                        print(f"--- Last Error: {engine.last_error_message} ---")
                    else:
                        print(f"--- An unspecified error occurred. Engine state: {engine.state.value} ---")
                elif hasattr(engine, 'last_error_message') and engine.last_error_message and engine.state != OrchestratorState.ERROR:
                    # If there's a lingering error message but state isn't ERROR, it might be stale or less critical
                    logger.debug(f"Status check: last_error_message is set ('{engine.last_error_message}') but state is {engine.state.name}")


            elif user_input.lower() == "stop":
                if engine.active_project and engine.state not in [OrchestratorState.IDLE, OrchestratorState.STOPPED, OrchestratorState.ERROR, OrchestratorState.TASK_COMPLETE]:
                    print("--- Attempting to stop the current task gracefully... ---")
                    engine.stop_current_task_gracefully()
                else:
                    print("--- No active task to stop, or engine is not in a stoppable state. ---")
            
            elif user_input.lower().startswith("goal "):
                if not engine.active_project:
                    print("--- No project selected. Use 'project select <name>' first. ---")
                else:
                    initial_instruction = user_input[len("goal "):].strip()
                    if initial_instruction:
                        print(f"--- Starting task for project '{active_project_name}' with goal: '{initial_instruction}' ---")
                        engine.start_task(initial_user_instruction=initial_instruction)
                    else:
                        print("--- Goal cannot be empty. Usage: goal <your goal> ---")
            
            # Default: Treat as an instruction if a project is active
            else:
                if engine.active_project:
                    print(f"--- Treating '{user_input}' as a new instruction for project '{active_project_name}' ---")
                    engine.start_task(initial_user_instruction=user_input)
                else:
                    print(f"--- Unknown command '{user_input}'. Type 'help' for available commands or 'project select <name>' to choose a project. ---")

            # After command processing, check and display engine state changes/messages
            # This is a simplified way; a more robust solution might involve callbacks or threads
            # For now, critical state changes are handled, and 'status' gives details.
            if engine.state == OrchestratorState.PAUSED_WAITING_USER_INPUT:
                 # This check is duplicated to ensure prompt appears immediately after state change
                if hasattr(engine, 'pending_user_question') and engine.pending_user_question:
                    print(f"--- Gemini Needs Input ---")
                    print(f"Question: {engine.pending_user_question}")
                    print("Use 'input <your response>' to reply.")
                else: # Should not happen if pending_user_question is always set
                    print("--- Engine is waiting for user input. Use 'input <your response>'. ---")
            
            elif engine.state == OrchestratorState.TASK_COMPLETE:
                print(f"--- Task marked as COMPLETE for project '{active_project_name}'. ---")
                # Optionally, reset current_task_goal or prompt for next action
            
            elif engine.state == OrchestratorState.ERROR:
                if hasattr(engine, 'last_error_message') and engine.last_error_message:
                    print(f"--- ENGINE ERROR: {engine.last_error_message} ---")
                else:
                    print(f"--- ENGINE ERROR: An unspecified error occurred. Check logs. ---")
                # Potentially stop or prompt user. For now, error is just reported.

        except KeyboardInterrupt:
            logger.info("\\n--- Keyboard interrupt detected. Shutting down... ---")
            print("\\n--- Keyboard interrupt detected. Shutting down... ---")
            if engine: # Ensure engine exists before calling shutdown
                engine.shutdown()
            break
        except EOFError: # Handle Ctrl+D
            logger.info("\\n--- EOF detected. Shutting down... ---")
            print("\\n--- EOF detected. Shutting down... ---")
            if engine:
                engine.shutdown()
            break
        except Exception as e:
            logger.critical(f"--- An unexpected error occurred in the terminal interface: {e} ---", exc_info=True)
            print(f"--- An unexpected error occurred in the terminal interface: {e} ---")
            traceback.print_exc()
            # Decide if to continue or break; for now, let's try to continue
            # but if engine is problematic, it might be better to break.

if __name__ == "__main__":
    # ensure_app_data_scaffolding() is called after logger is available if we want its output logged.
    # For now, keeping it simple. Logging for it will be via its own calls to logger.
    
    logger.info("Orchestrator Prime starting...")
    ensure_app_data_scaffolding() # Now its prints will be logger.info calls

    engine_instance = None
    try:
        engine_instance = OrchestrationEngine()
        run_terminal_interface(engine_instance)

    except FileNotFoundError as e:
        logger.critical(f"Missing critical file, likely config.ini. Details: {e}", exc_info=True)
        print(f"ERROR (main): Missing critical file, likely config.ini. Details: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
    except ValueError as e:
        logger.critical(f"Configuration or Value error. Details: {e}", exc_info=True)
        print(f"ERROR (main): Configuration or Value error. Details: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
    except ImportError as e:
        logger.critical(f"Missing dependency. Details: {e}", exc_info=True)
        print(f"ERROR (main): Missing dependency. Details: {e}", file=sys.stderr)
        traceback.print_exc()
        missing_module_name = str(e).split("'")[-2] if "'" in str(e) else "a required library"
        print(f"Please install all dependencies from requirements.txt, especially '{missing_module_name}'.")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"CRITICAL UNEXPECTED ERROR (main) during startup or terminal loop: {e}", exc_info=True)
        print(f"CRITICAL UNEXPECTED ERROR (main) during startup or terminal loop: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
    finally:
        if engine_instance and not engine_instance._shutdown_complete: # Check if already shut down
            logger.info("Main: Ensuring engine shutdown...")
            print("Main: Ensuring engine shutdown...")
            engine_instance.shutdown()
        logger.info("Main: Application exited.")
        print("Main: Application exited.") 
