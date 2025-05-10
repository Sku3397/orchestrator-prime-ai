import sys
print("DEBUG: main.py script started.", file=sys.stderr)
import os
import traceback
import logging
print("DEBUG main.py: After os, traceback, logging imports", file=sys.stderr)
from engine import OrchestrationEngine, EngineState
print("DEBUG main.py: After engine import", file=sys.stderr)
from persistence import load_projects, add_project, Project
print("DEBUG main.py: After persistence import", file=sys.stderr)
from models import OrchestratorState
print("DEBUG main.py: After models import", file=sys.stderr)

# --- Setup Logging ---
logger = logging.getLogger("orchestrator_prime")
logger.setLevel(logging.DEBUG)
fh = logging.FileHandler("orchestrator_prime.log", mode='w')
fh.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s')
fh.setFormatter(formatter)
ch.setFormatter(formatter)
logger.addHandler(fh)
logger.addHandler(ch)
print("DEBUG main.py: Logging configured.", file=sys.stderr)
# --- End Logging Setup ---

def ensure_app_data_scaffolding():
    print("DEBUG main.py: Inside ensure_app_data_scaffolding - Start", file=sys.stderr)
    app_data_dir = "app_data"
    projects_json_path = os.path.join(app_data_dir, "projects.json")
    try:
        if not os.path.exists(app_data_dir):
            os.makedirs(app_data_dir)
            logger.info(f"Created directory: {app_data_dir}")
        if not os.path.exists(projects_json_path):
            with open(projects_json_path, 'w') as f:
                f.write("[]")
            logger.info(f"Created empty projects file: {projects_json_path}")
    except OSError as e:
        logger.critical(f"CRITICAL ERROR: Could not create app_data directory or projects.json: {e}")
        print(f"DEBUG main.py: ensure_app_data_scaffolding - CRITICAL ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print("DEBUG main.py: Inside ensure_app_data_scaffolding - End", file=sys.stderr)

def print_welcome():
    print("\nWelcome to Orchestrator Prime (Terminal Edition)")
    print("-------------------------------------------------")
    print("Type 'help' for a list of commands.")

def print_help():
    print("\nAvailable Commands:")
    print("  project list                - List all available projects.")
    print("  project add                 - Add a new project.")
    print("  project select <name>       - Select an active project.")
    print("  goal <initial goal text>    - Set the initial goal for the selected project and start.")
    print("  input <response text>       - Provide input when Gemini is waiting.")
    print("  status                      - Display the current engine status and active project.")
    print("  stop                        - Stop the current task gracefully.")
    print("  quit                        - Shutdown Orchestrator Prime and exit.")
    print("  help                        - Show this help message.")
    print("\nAny other input will be treated as a new goal/instruction for the active project if one is selected.")

def run_terminal_interface(engine: OrchestrationEngine):
    print_welcome()
    active_project_name = None
    if engine.current_project: # Check if engine loaded a project
        active_project_name = engine.current_project.name
        print(f"--- Automatically selected last active project: {active_project_name} ---")


    while True:
        prompt_project_name = f" (Project: {active_project_name})" if active_project_name else ""
        # Ensure prompt has a newline for readline compatibility in tests
        prompt = f"OP{prompt_project_name} > \n" 
        
        try:
            # Use sys.stdin.readline() for consistency with minimal test, though input() is usually fine.
            # However, for testing, readline() might be more robust if input() behaves differently with pipes.
            # For interactive use, input() is better as it handles prompts nicely.
            # Let's stick to input() for now as it's the standard.
            # The key was the newline in the prompt string itself.
            print(prompt, end='') # Print prompt without extra newline from print()
            sys.stdout.flush() # Ensure prompt is sent
            user_input = sys.stdin.readline().strip()


            if not user_input: # Handle EOF or empty line if readline() is used differently.
                # If input() is used, an empty line is just an empty string.
                # If sys.stdin.readline() and user just hits enter, it's '\n', strip makes it empty.
                # If process is fed EOF, readline() returns empty string.
                if not active_project_name: # if no project selected, EOF might mean quit
                    print("DEBUG main.py: EOF or empty input with no project selected, treating as quit.", file=sys.stderr)
                    engine.shutdown()
                    break
                continue


            if engine.state == EngineState.PAUSED_WAITING_USER_INPUT:
                if user_input.lower().startswith("input "):
                    response = user_input[len("input "):].strip()
                    if response:
                        print(f"--- Resuming with your input: '{response}' ---")
                        engine.resume_with_user_input(response)
                    else:
                        print("--- Input cannot be empty. Please provide a response. ---")
                else:
                    print(f"--- Engine is waiting for input. Use 'input <your response>' or see 'help'. ---")
                    if hasattr(engine, 'pending_user_question') and engine.pending_user_question:
                         print(f"Gemini's Question: {engine.pending_user_question}")
                    continue

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
                print("Project Name:", flush=True) # Ensure newline and flush
                name = sys.stdin.readline().strip()
                while not name:
                    print("Project Name (cannot be empty):", flush=True) # Ensure newline and flush
                    name = sys.stdin.readline().strip()
                
                print("Workspace Root Path:", flush=True) # Ensure newline and flush
                root_path = sys.stdin.readline().strip()
                loop_count = 0
                while not os.path.isdir(root_path):
                    loop_count += 1
                    logger.debug(f"Path validation loop iter {loop_count}: Received path '{root_path}', os.path.isdir() is {os.path.isdir(root_path)}. Type: {type(root_path)}")
                    # Attempt to normalize/resolve path before checking
                    normalized_path = os.path.abspath(os.path.expanduser(root_path))
                    logger.debug(f"Path validation loop iter {loop_count}: Normalized path to '{normalized_path}', os.path.isdir() is {os.path.isdir(normalized_path)}.")
                    if os.path.isdir(normalized_path):
                        root_path = normalized_path # Use the normalized path if it's valid
                        break # Exit loop if normalized path is a directory
                    
                    print("Invalid path. Workspace Root Path (must be an existing directory):", flush=True) # Ensure newline and flush
                    if loop_count > 3: # Prevent infinite loop in tests if there is a persistent issue
                        logger.warning(f"Path validation loop exceeded 3 retries for path: '{root_path}'. Breaking to avoid hang.")
                        # Attempt to use a default path to allow test to proceed if possible, or handle error
                        # This is a test-specific workaround if actual input is problematic
                        # For now, let it fail through if it still doesn't validate.
                        # Consider raising an exception or using a predefined valid path for testing if it always fails here.
                        break 
                    root_path = sys.stdin.readline().strip()
                
                # Final check after loop, in case loop broke due to retry limit
                if not os.path.isdir(root_path):
                    logger.error(f"Project Add: Final path '{root_path}' is still not a valid directory after retry loop. Aborting add.")
                    # Return to main prompt or handle error gracefully without adding
                    print(f"--- Could not validate workspace path '{root_path}'. Project not added. ---")
                    continue # Using continue to go to the next iteration of the main while loop

                print("Overall Goal for the project:", flush=True) # Ensure newline and flush
                goal = sys.stdin.readline().strip()
                while not goal:
                    print("Overall Goal (cannot be empty):", flush=True) # Ensure newline and flush
                    goal = sys.stdin.readline().strip()

                new_project = Project(name=name, workspace_root_path=root_path, overall_goal=goal)
                try:
                    add_project(new_project) # This also saves projects
                    print(f"--- Project '{name}' added successfully. ---")
                except Exception as e:
                    print(f"--- Error adding project: {e} ---")
            
            elif user_input.lower().startswith("project select "):
                name_to_select = user_input[len("project select "):].strip()
                if name_to_select:
                    try:
                        if engine.set_active_project(name_to_select):
                            active_project_name = name_to_select # Update local active_project_name
                            print(f"--- Project '{active_project_name}' selected. ---")
                        else:
                            # set_active_project now logs more details, so this can be simpler
                            print(f"--- Could not select project '{name_to_select}'. See logs for details. ---")
                    except Exception as e: # Should be caught by engine ideally
                        print(f"--- Error selecting project '{name_to_select}': {e} ---")
                        logger.error(f"Error in main during project select: {e}", exc_info=True)
                else:
                    print("--- Please specify a project name to select. Usage: project select <name> ---")

            elif user_input.lower() == "status":
                print_status(engine)
            
            elif user_input.lower() == "stop":
                if engine.current_project and engine.state not in [EngineState.IDLE, EngineState.STOPPED, EngineState.ERROR, EngineState.TASK_COMPLETE]:
                    print("--- Attempting to stop the current task gracefully... ---")
                    engine.stop_current_task_gracefully()
                else:
                    print("--- No active task to stop, or engine is not in a stoppable state. ---")
            
            elif user_input.lower() == "_reload_gemini_client":
                # This is a hidden command for testing/debugging to reload the gemini client in the engine
                if engine.reinitialize_gemini_client():
                    print("Engine's Gemini client re-initialization attempted successfully.", flush=True)
                else:
                    print("Engine's Gemini client re-initialization failed. Check logs.", flush=True)
            
            elif user_input.lower().startswith("goal "):
                if not engine.current_project:
                    print("--- No project selected. Use 'project select <name>' first. ---")
                else:
                    initial_instruction = user_input[len("goal "):].strip()
                    if initial_instruction:
                        print(f"--- Starting task for project '{active_project_name}' with goal: '{initial_instruction}' ---")
                        engine.start_task(initial_user_instruction=initial_instruction)
                    else:
                        print("--- Goal cannot be empty. Usage: goal <your goal> ---")
            
            else: # Default: Treat as an instruction if a project is active
                if engine.current_project:
                    # If engine is PAUSED_WAITING_USER_INPUT, this 'else' block shouldn't be hit due to earlier check.
                    # So, this implies a new instruction or continuation when not explicitly waiting for input.
                    print(f"--- Treating '{user_input}' as a new instruction for project '{active_project_name}' ---")
                    engine.start_task(initial_user_instruction=user_input) # Or a more generic 'handle_instruction'
                else:
                    print(f"--- Unknown command '{user_input}'. Type 'help' for available commands or 'project select <name>' to choose a project. ---")

            # Post-command state checks (mainly for asynchronous changes)
            if engine.state == EngineState.PAUSED_WAITING_USER_INPUT:
                if hasattr(engine, 'pending_user_question') and engine.pending_user_question:
                    print(f"--- Gemini Needs Input ---")
                    print(f"Question: {engine.pending_user_question}")
                    print("Use 'input <your response>' to reply.")
                else: 
                    print("--- Engine is waiting for user input. Use 'input <your response>'. ---")
            
            elif engine.state == EngineState.TASK_COMPLETE:
                print(f"--- Task marked as COMPLETE for project '{active_project_name}'. ---")
            
            elif engine.state == EngineState.ERROR:
                if hasattr(engine, 'last_error_message') and engine.last_error_message:
                    print(f"--- ENGINE ERROR: {engine.last_error_message} ---")
                else:
                    print(f"--- ENGINE ERROR: An unspecified error occurred. Check logs. ---")

        except KeyboardInterrupt:
            print("\n--- Keyboard interrupt detected. Shutting down... ---")
            logger.info("Keyboard interrupt detected by user in main loop.")
            if engine: 
                engine.shutdown()
            break
        except EOFError: 
            print("\n--- EOF detected. Shutting down... ---")
            logger.info("EOF detected in main loop (likely piped input ended or Ctrl+D).")
            if engine:
                engine.shutdown()
            break
        except Exception as e:
            logger.critical(f"--- An unexpected error occurred in the terminal interface: {e} ---", exc_info=True)
            print(f"--- An unexpected error occurred: {e} ---") # Keep it simple for user
            traceback.print_exc() # For dev console, if visible

def print_status(engine: OrchestrationEngine):
    active_project_state_obj = engine.current_project_state
    print("\n--- Orchestrator Prime Status ---")
    print(f"Engine Status: {engine.state.name}")
    if engine.current_project and active_project_state_obj:
        print(f"Active Project: {engine.current_project.name} (ID: {engine.current_project.id})")
        print(f"  Workspace: {engine.current_project.workspace_root_path}")
        print(f"  Current Goal: {active_project_state_obj.current_goal if active_project_state_obj.current_goal else 'N/A'}")
        
        pending_question = engine.pending_user_question
        if pending_question: # Check if there's actually a question
            print(f"    Pending Question from Gemini: {pending_question}")
        
        # Display conversation history snippet if available
        if active_project_state_obj.conversation_history:
            print(f"  Conversation History: {len(active_project_state_obj.conversation_history)} turns")
            # last_few_turns = active_project_state_obj.conversation_history[-3:] # Last 3 turns
            # for i, turn in enumerate(last_few_turns):
            #     print(f"    Turn -{len(last_few_turns)-i}: [{turn.sender}] {turn.message[:70]}...")
        else:
            print("  Conversation History: Empty")

        print(f"  Gemini Turns Since Last Summary: {active_project_state_obj.gemini_turns_since_last_summary}")
        if active_project_state_obj.context_summary:
             print(f"  Context Summary (length): {len(active_project_state_obj.context_summary)} chars")
        else:
             print("  Context Summary: Not yet generated.")

    else:
        print("No project is currently active.")

    # Display last error if any, regardless of current state (could be a past error)
    if hasattr(engine, '_last_critical_error') and engine._last_critical_error: # Check internal attribute if it exists
         print(f"--- Last Critical Engine Error: {engine._last_critical_error} ---")
    elif hasattr(engine, 'last_error_message') and engine.last_error_message and engine.state != EngineState.ERROR:
         # If there's a non-critical error message logged by the engine
         print(f"--- Last Engine Message: {engine.last_error_message} ---")


if __name__ == "__main__":
    print("DEBUG main.py: __main__ block entered", file=sys.stderr)
    sys.stderr.flush()
    engine_instance = None # Define engine_instance here for finally block
    try:
        print("DEBUG main.py: Before ensure_app_data_scaffolding() call", file=sys.stderr)
        sys.stderr.flush()
        ensure_app_data_scaffolding()
        print("DEBUG main.py: After ensure_app_data_scaffolding() call", file=sys.stderr)
        sys.stderr.flush()
        
        logger.info("Orchestrator Prime starting...") # Moved after ensure_app_data for log file
        print("DEBUG main.py: Logger 'Orchestrator Prime starting...' sent.", file=sys.stderr)
        sys.stderr.flush()

        print("DEBUG main.py: Before OrchestrationEngine() instantiation", file=sys.stderr)
        sys.stderr.flush()
        engine_instance = OrchestrationEngine()
        print("DEBUG main.py: After OrchestrationEngine() instantiation", file=sys.stderr)
        sys.stderr.flush()

        print("DEBUG main.py: Before run_terminal_interface() call", file=sys.stderr)
        sys.stderr.flush()
        run_terminal_interface(engine_instance)
        print("DEBUG main.py: After run_terminal_interface() call - normally not reached unless quit issues prompt", file=sys.stderr)
        sys.stderr.flush()

    except Exception as e:
        # This is a top-level catch for unexpected errors during setup or in run_terminal_interface
        critical_error_msg = f"CRITICAL UNHANDLED EXCEPTION in main: {e}"
        print(critical_error_msg, file=sys.stderr) # Print to stderr for immediate visibility
        sys.stderr.flush()
        if logger: # Check if logger was initialized
            logger.critical(critical_error_msg, exc_info=True)
        else: # Fallback if logger itself failed
            traceback.print_exc(file=sys.stderr)
        sys.exit(1) # Exit on critical unhandled error in main setup
    finally:
        # Ensure engine shutdown is attempted if instance was created
        if engine_instance and hasattr(engine_instance, '_shutdown_complete') and not engine_instance._shutdown_complete:
            print("DEBUG main.py: Ensuring engine shutdown in finally block.", file=sys.stderr)
            sys.stderr.flush()
            engine_instance.shutdown()
        
        final_msg = "Orchestrator Prime shutdown sequence complete from main."
        print(f"DEBUG main.py: {final_msg}", file=sys.stderr)
        sys.stderr.flush()
        if logger: # Check if logger was initialized
            logger.info(final_msg)
