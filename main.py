import sys
import json

def print_to_stderr(message: str):
    print(f"DEBUG: print_to_stderr called with: {message[:50]}...", flush=True) # For tracing
    try:
        if sys.stderr and hasattr(sys.stderr, 'writable') and sys.stderr.writable() and not sys.stderr.closed:
            print(message, file=sys.stderr, flush=True)
            print(f"DEBUG: Message printed to actual sys.stderr.", flush=True) # For tracing
        else:
            status_str = f"stderr_None: {sys.stderr is None}, "
            status_str += f"stderr_closed: {sys.stderr.closed if hasattr(sys.stderr, 'closed') else 'N/A'}, "
            status_str += f"stderr_writable: {sys.stderr.writable() if hasattr(sys.stderr, 'writable') else 'N/A'}"
            print(f"STDERR_FALLBACK ({status_str}): {message}", flush=True)
    except Exception as e:
        print(f"STDERR_PRINT_ERROR: Could not print message '{message[:50]}...' due to: {type(e).__name__} - {e}", flush=True)

print_to_stderr("DEBUG: main.py script started.")
import os
import traceback
import logging
print_to_stderr("DEBUG main.py: After os, traceback, logging imports")
from engine import OrchestrationEngine, EngineState
print_to_stderr("DEBUG main.py: After engine import")
from persistence import load_projects, add_project, Project, PersistenceError, DuplicateProjectError
print_to_stderr("DEBUG main.py: After persistence import")
from models import OrchestratorState
print_to_stderr("DEBUG main.py: After models import")

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
print_to_stderr("DEBUG main.py: Logging configured.")
# --- End Logging Setup ---

def ensure_app_data_scaffolding():
    print_to_stderr("DEBUG main.py: Inside ensure_app_data_scaffolding - Start")
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
        print_to_stderr(f"DEBUG main.py: ensure_app_data_scaffolding - CRITICAL ERROR: {e}")
        sys.exit(1)
    print_to_stderr("DEBUG main.py: Inside ensure_app_data_scaffolding - End")

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
                    print_to_stderr("DEBUG main.py: EOF or empty input with no project selected, treating as quit.")
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
                    added_project_obj = add_project(new_project) # This also saves projects
                    # add_project now returns the project or raises error
                    if added_project_obj:
                        print(f"--- Project '{name}' added successfully. ---")
                    else:
                        # This case should ideally not be reached if add_project raises errors on failure
                        print(f"--- Failed to add project '{name}'. Reason unknown (add_project returned None). ---")
                except DuplicateProjectError as e:
                    print(f"--- Error adding project: {e} ---") # TC5 expects this format
                except PersistenceError as e:
                    print(f"--- Persistence error adding project: {e} ---")
                except Exception as e:
                    # General catch-all for other unexpected errors during add_project call
                    print(f"--- An unexpected error occurred while adding project: {e} ---")
            
            elif user_input.lower().startswith("project select ") or user_input.lower() == "project select":
                name_to_select = user_input[len("project select"):].strip()
                if name_to_select:
                    try:
                        if engine.set_active_project(name_to_select):
                            active_project_name = name_to_select # Update local active_project_name
                            print(f"--- Project '{active_project_name}' selected. ---")
                            # --- PATCH: Print prompt and status after selection ---
                            print(f"OP (Project: {active_project_name}) > ")
                            print_status(engine)
                        else:
                            # set_active_project now logs more details, so this can be simpler
                            print(f"--- Could not select project '{name_to_select}'. See logs for details. ---")
                            # --- FIX: Reset active_project_name if selection failed ---
                            active_project_name = None
                    except Exception as e: # Should be caught by engine ideally
                        print(f"--- Error selecting project '{name_to_select}': {e}" )
                        logger.error(f"Error in main during project select: {e}", exc_info=True)
                        # --- FIX: Reset active_project_name on exception ---
                        active_project_name = None
                else: # No name provided, meaning "project select" was entered alone for deselection
                    if active_project_name:
                        print(f"--- Deselecting active project: {active_project_name} ---", flush=True)
                        engine.set_active_project(None) # Signal engine to deselect
                        active_project_name = None
                        print("--- Active project cleared. ---", flush=True)
                    else:
                        print("--- No project currently selected. ---", flush=True)

            elif user_input.lower() == "status":
                print_status(engine)
                # --- FIX: Synchronize prompt with engine state after status ---
                if engine.current_project is not None:
                    active_project_name = engine.current_project.name
                else:
                    active_project_name = None
            
            elif user_input.lower() == "stop":
                if engine.current_project and engine.state not in [EngineState.IDLE, EngineState.STOPPED, EngineState.ERROR, EngineState.TASK_COMPLETE]:
                    print("--- Attempting to stop the current task gracefully... ---")
                    engine.stop_current_task_gracefully()
                else:
                    print("--- No active task to stop, or engine is not in a stoppable state. ---")
            
            elif user_input.lower() == "_show_full_history":
                if engine.current_project and engine.current_project_state:
                    print("--- Full Conversation History ---")
                    for i, turn in enumerate(engine.current_project_state.conversation_history):
                        print(f"Turn {i+1} ({turn.sender}): {turn.message}")
                        if turn.metadata:
                            print(f"  Metadata: {turn.metadata}")
                    print("--- End of History ---")
                else:
                    print("No active project or history to show.")
                continue
            elif user_input.lower() == "_force_summarize":
                if engine.current_project and engine.current_project_state:
                    print("Forcing summarization attempt...")
                    if engine._check_and_run_summarization(force_summarize=True):
                        print(f"Summarization run. New summary length: {len(engine.current_project_state.context_summary) if engine.current_project_state.context_summary else 0}")
                    else:
                        print("Summarization not run (e.g. not enough turns or error).")
                else:
                    print("No active project to summarize.")
                continue
            elif user_input.lower().startswith("_apply_mock"):
                parts = user_input.split(maxsplit=2)
                if len(parts) >= 2:
                    mock_type = parts[1]
                    details_json = parts[2] if len(parts) > 2 else "null"
                    try:
                        details = json.loads(details_json)
                        if engine.apply_mock_communicator(mock_type, details):
                            print(f"Applied mock communicator: '{mock_type}' with details: {details}.", flush=True)
                        else:
                            print(f"Failed to apply mock communicator: '{mock_type}'. Check logs.", flush=True)
                    except json.JSONDecodeError:
                        print(f"Invalid JSON details for _apply_mock: {details_json}", flush=True)
                    except Exception as e:
                        print(f"Error processing _apply_mock: {e}", flush=True)
                else:
                    print("Usage: _apply_mock <mock_type> [json_details]", flush=True)
                continue
            elif user_input.lower() == "_reload_gemini_client": # This now restores the REAL client
                if engine.reinitialize_gemini_client(): # This method now specifically loads the REAL client
                    print("Engine's REAL Gemini client re-initialization attempted successfully.", flush=True)
                else:
                    print("Engine's REAL Gemini client re-initialization failed. Check logs.", flush=True)
                continue
            
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
            
            else:
                # Check if the input matches any known command
                known_commands = [
                    "help", "project list", "project add", "project select", "goal", "input", "status", "stop", "quit"
                ]
                is_known = False
                for cmd_prefix in known_commands: # Iterate through known command prefixes
                    if user_input.lower().startswith(cmd_prefix):
                        # Exact match or command with arguments
                        if user_input.lower() == cmd_prefix or user_input.lower().startswith(cmd_prefix + " "):
                            is_known = True
                            break
                if not is_known:
                    # If it's not a known command and a project is active, treat as an implicit goal.
                    if active_project_name:
                        print(f"--- Treating '{user_input}' as new goal/instruction for project '{active_project_name}' ---")
                        try:
                            engine.start_task(user_input)
                        except Exception as e:
                            print(f"--- Error starting task: {e} ---")
                    else:
                        print(f"--- Unknown command '{user_input}'. Type 'help' for available commands or 'project select <name>' to choose a project. ---")
                    continue # explicit continue after handling unknown/implicit goal

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

            if engine.state in [EngineState.RUNNING_CALLING_GEMINI,
                                EngineState.RUNNING_WAITING_INITIAL_GEMINI,
                                EngineState.RUNNING_PROCESSING_LOG,
                                EngineState.SUMMARIZING_CONTEXT]:
                logger.debug(f"MAIN_LOOP_TRACE: Engine state is {engine.state.name}. Checking Gemini queue.")
                # Non-blocking check for Gemini response
                try:
                    gemini_response = engine.check_gemini_response_queue_non_blocking()
                    if gemini_response:
                        logger.info(f"MAIN_LOOP_TRACE: Got Gemini response from queue. About to handle: {str(gemini_response)[:100]}...")
                        engine.handle_gemini_response_from_main(gemini_response)
                    else:
                       logger.debug(f"MAIN_LOOP_TRACE: Gemini queue was empty for state {engine.state.name}.")
                except Exception as e_queue:
                    logger.error(f"Error checking/handling Gemini response queue: {e_queue}", exc_info=True)
                    engine.main_loop_error_handler(f"Queue error: {e_queue}")

            # Display intermediate status if a longer operation
            if engine.state == EngineState.RUNNING_CALLING_GEMINI or engine.state == EngineState.RUNNING_WAITING_INITIAL_GEMINI:
                print(f"--- Gemini is waiting for response. ---")
            elif engine.state == EngineState.RUNNING_PROCESSING_LOG:
                print(f"--- Gemini is processing a log. ---")
            elif engine.state == EngineState.SUMMARIZING_CONTEXT:
                print(f"--- Gemini is summarizing context. ---")

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
    print("\n--- Orchestrator Prime Status ---")
    if engine.current_project:
        print(f"Active Project: {engine.current_project.name} (ID: {engine.current_project.id})")
        print(f"Workspace: {engine.current_project.workspace_root_path}")
        print(f"Overall Goal: {engine.current_project.overall_goal}")
        if engine.current_project_state:
            print(f"Conversation Turns: {len(engine.current_project_state.conversation_history)}")
            print(f"Context Summary Length: {len(engine.current_project_state.context_summary) if engine.current_project_state.context_summary else 0} chars")
            print(f"Last Instruction to Cursor: '{engine.current_project_state.last_instruction_sent[:70]}...'" if engine.current_project_state.last_instruction_sent else "None")
    else:
        print("Active Project: None")

    print(f"Engine Status: {engine.get_current_engine_state_name()}") # Use new getter

    # Display the special status message if set by the engine
    if hasattr(engine, 'status_message_for_display') and engine.status_message_for_display:
        print(f"Engine Message: {engine.status_message_for_display}")
        engine.status_message_for_display = None # Clear after displaying

    if engine.state == EngineState.ERROR and engine.last_error_message:
        print(f"Error Message: {engine.last_error_message}")
    elif engine.state == EngineState.PAUSED_WAITING_USER_INPUT and engine.pending_user_question:
        print(f"Gemini Needs Input: {engine.pending_user_question}")
    print("-------------------------------")


if __name__ == "__main__":
    print_to_stderr("DEBUG main.py: __main__ block entered")
    sys.stderr.flush()
    engine_instance = None # Define engine_instance here for finally block
    try:
        print_to_stderr("DEBUG main.py: Before ensure_app_data_scaffolding() call")
        sys.stderr.flush()
        ensure_app_data_scaffolding()
        print_to_stderr("DEBUG main.py: After ensure_app_data_scaffolding() call")
        sys.stderr.flush()
        
        logger.info("Orchestrator Prime starting...") # Moved after ensure_app_data for log file
        print_to_stderr("DEBUG main.py: Logger 'Orchestrator Prime starting...' sent.")
        sys.stderr.flush()

        print_to_stderr("DEBUG main.py: Before OrchestrationEngine() instantiation")
        sys.stderr.flush()
        engine_instance = OrchestrationEngine()
        print_to_stderr("DEBUG main.py: After OrchestrationEngine() instantiation")
        sys.stderr.flush()

        print_to_stderr("DEBUG main.py: Before run_terminal_interface() call")
        sys.stderr.flush()
        run_terminal_interface(engine_instance)
        print_to_stderr("DEBUG main.py: After run_terminal_interface() call - normally not reached unless quit issues prompt")
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
            print_to_stderr("DEBUG main.py: Ensuring engine shutdown in finally block.")
            sys.stderr.flush()
            engine_instance.shutdown()
        
        final_msg = "Orchestrator Prime shutdown sequence complete from main."
        print_to_stderr(f"DEBUG main.py: {final_msg}")
        sys.stderr.flush()
        if logger: # Check if logger was initialized
            logger.info(final_msg)
