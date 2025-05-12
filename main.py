import sys
import json
import time
import queue
import traceback
import logging
import os
import threading
from pathlib import Path
import configparser

# Add a very early, unconditional print statement to sys.stderr
print("DEBUG main.py: VERY EARLY STARTING", file=sys.stderr)
sys.stderr.flush()

# Add print for current working directory and sys.path
print(f"DEBUG main.py: Current Working Directory: {os.getcwd()}", file=sys.stderr)
print(f"DEBUG main.py: sys.path: {sys.path}", file=sys.stderr)
sys.stderr.flush()

def print_to_stderr(message: str):
    # Use a different prefix here to distinguish from test_terminal_app logs
    print(f"MAIN_DEBUG: {message}", file=sys.stderr, flush=True)

# Print this immediately after defining print_to_stderr
print_to_stderr("main.py script started and print_to_stderr defined.")


import traceback
import logging
print_to_stderr("After os, traceback, logging imports")

# Add debug print before importing engine
print_to_stderr("Before importing engine")
from engine import OrchestrationEngine, EngineState
print_to_stderr("After engine import")

# Add debug print before importing persistence
print_to_stderr("Before importing persistence")
from persistence import load_projects, add_project, Project, PersistenceError, DuplicateProjectError
print_to_stderr("After persistence import")

# Add debug print before importing models
print_to_stderr("Before importing models")
from models import OrchestratorState
print_to_stderr("After models import")

# Add debug print before config import
print_to_stderr("Before importing config_manager")

try:
    # Add debug logging to list current directory contents
    current_dir_contents = os.listdir('.')
    print_to_stderr(f"Contents of current directory (.): {current_dir_contents}")
    # Add debug logging to list contents of relevant sys.path directories (assuming config is in project root)
    if '.' in sys.path:
        print_to_stderr("'.' is in sys.path.")
    else:
        print_to_stderr(f"'.' is NOT in sys.path. sys.path: {sys.path}")

    from config_manager import ConfigManager
    # Add debug print after successful config import
    print_to_stderr("Successfully imported ConfigManager")

except ImportError as e:
    print_to_stderr(f"CRITICAL ERROR: Failed to import config_manager: {e}")
    sys.exit(1) # Exit immediately on config import failure
except Exception as e:
    print_to_stderr(f"CRITICAL UNHANDLED EXCEPTION during config_manager import: {e}")
    traceback.print_exc(file=sys.stderr)
    sys.exit(1) # Exit on any other unexpected error during config import

print_to_stderr("After config_manager import")

# --- Setup Logging ---
logger = logging.getLogger("orchestrator_prime")
# Configure logging levels and handlers as before
logger.setLevel(logging.DEBUG)
fh = logging.FileHandler("orchestrator_prime.log", mode='w')
fh.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO) # Console handler INFO level by default
# Use a different formatter for console to be less verbose, but keep detailed for file
file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s')
console_formatter = logging.Formatter('%(levelname)s - %(message)s')
fh.setFormatter(file_formatter)
ch.setFormatter(console_formatter)

# Remove default handlers to avoid duplicate messages if basicConfig was already called
if logger.hasHandlers():
    logger.handlers.clear()

logger.addHandler(fh)
logger.addHandler(ch)

# Test logging setup
logger.debug("Logging debug test message.")
logger.info("Logging info test message.")
logger.warning("Logging warning test message.")
logger.error("Logging error test message.")
logger.critical("Logging critical test message.")

print_to_stderr("Logging configured and tested.")
# --- End Logging Setup ---

def ensure_app_data_scaffolding():
    print_to_stderr("Inside ensure_app_data_scaffolding - Start")
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
        print_to_stderr(f"ensure_app_data_scaffolding - CRITICAL OS ERROR: {e}")
        sys.exit(1) # Exit on critical scaffolding error
    except Exception as e:
         logger.critical(f"CRITICAL UNHANDLED EXCEPTION during scaffolding: {e}")
         print_to_stderr(f"ensure_app_data_scaffolding - CRITICAL UNHANDLED EXCEPTION: {e}")
         traceback.print_exc(file=sys.stderr)
         sys.exit(1) # Exit on any other unhandled exception during scaffolding

    print_to_stderr("Inside ensure_app_data_scaffolding - End")

def print_welcome():
    print_to_stderr("Inside print_welcome - Start") # Debug log
    print("\nWelcome to Orchestrator Prime (Terminal Edition)")
    print("-------------------------------------------------")
    print("Type 'help' for a list of commands.")
    print_to_stderr("Inside print_welcome - End") # Debug log

def print_help():
    print_to_stderr("Inside print_help - Start") # Debug log
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
    print_to_stderr("Inside print_help - End") # Debug log

def run_terminal_interface(engine: OrchestrationEngine):
    # Add a small delay at the beginning for subprocess stream initialization robustness
    time.sleep(0.1) # Small delay
    print_to_stderr("Entering run_terminal_interface.") # Added debug log
    
    print_to_stderr("Before print_welcome() in run_terminal_interface") # Added this debug log
    print_welcome()
    print_to_stderr("After print_welcome() in run_terminal_interface") # Added this debug log
    
    active_project_name = None
    if engine.current_project: # Check if engine loaded a project
        active_project_name = engine.current_project.name
        print(f"--- Automatically selected last active project: {active_project_name} ---")

    print_to_stderr("Before main input loop (while True) in run_terminal_interface.") # Added this debug log
    while True:
        print_to_stderr("Top of main input loop in run_terminal_interface.") # Added debug log
        prompt_project_name = f" (Project: {active_project_name})" if active_project_name else ""
        # Ensure prompt has a newline for readline compatibility in tests
        prompt = f"OP{prompt_project_name} > \n" 
        
        print_to_stderr(f"About to print prompt: '{prompt.strip()}' in run_terminal_interface.") # Added debug log before printing prompt
        try:
            # Use sys.stdin.readline() for consistency with minimal test, though input() is usually fine.
            # However, for testing, readline() might be more robust if input() behaves differently with pipes.
            # For interactive use, input() is better as it handles prompts nicely.
            # The key was the newline in the prompt string itself.
            print(prompt, end='') # Print prompt without extra newline from print()
            sys.stdout.flush() # Ensure prompt is sent
            print_to_stderr("Reading user input in run_terminal_interface...") # Added debug log before reading input
            read_line_result = sys.stdin.readline()
            print_to_stderr(f"sys.stdin.readline() raw result: {repr(read_line_result)} (length {len(read_line_result)}).")
            print_to_stderr(f"sys.stdin.readline() returned '{read_line_result.strip()}' (raw length {len(read_line_result)}).")
            user_input = read_line_result.strip()
            # print_to_stderr(f"Received user input: '{user_input}' in run_terminal_interface.") # Added debug log after reading input

            # Process the command using the engine
            command_processed = engine.process_command(user_input)

            # If the command wasn't processed internally by the engine (e.g., not a built-in command)
            # and a project is selected, treat it as a new instruction.
            if not command_processed and active_project_name and engine.state != EngineState.PAUSED_WAITING_USER_INPUT:
                 print(f"--- Received new instruction for '{active_project_name}'. Processing... ---")
                 try:
                      # This is where the main loop logic for handling unrecognied input goes.
                      # Need to add a method to the engine to handle arbitrary user input as an instruction.
                      # For now, just print a message.
                      # Placeholder: engine.handle_user_instruction(user_input)
                      print(f"NOTE: Unrecognized command/instruction for project '{active_project_name}'. Processing not yet fully implemented via engine.process_command fallback.")
                      pass
                 except Exception as e:
                      print(f"--- An error occurred while processing user instruction: {e} ---")
                      logger.error(f"Error handling user instruction for '{active_project_name}': {e}", exc_info=True)
            elif not command_processed and not active_project_name:
                 # If no project is selected and it's not a recognized command
                 print("--- Invalid command or no project selected. Type 'help' for commands or 'project select <name>' to choose a project. ---")

            # Re-check active project name as it might have changed after processing a command (e.g., project select)
            active_project_name = engine.current_project.name if engine.current_project else None

            # After processing a command/input, check for engine state changes or output
            # The engine runs its loop in a separate thread, but state changes and output
            # are communicated back to the main thread/loop.
            # In a typical interactive terminal, we would just wait for the next prompt.
            # For testing, we might need to check engine state or output queues more actively,
            # but the primary interaction is via commands and waiting for the next prompt.
            # The engine's internal loop handles processing and state transitions.
            pass # Continue the while loop to wait for the next input

        except EOFError:
             print_to_stderr("DEBUG main.py: Received EOFError on stdin. Shutting down.") # Added debug log
             print("--- Received EOF. Shutting down... ---")
             engine.shutdown()
             break # Exit loop on EOF
        except KeyboardInterrupt:
             print_to_stderr("DEBUG main.py: Received KeyboardInterrupt. Shutting down.") # Added debug log
             print("--- Received KeyboardInterrupt. Shutting down gracefully... ---")
             engine.shutdown()
             break # Exit loop on Ctrl+C
        except Exception as e:
             print_to_stderr(f"DEBUG main.py: Unhandled exception in main input loop: {e}") # Added debug log
             print(f"--- An unhandled error occurred: {e} ---")
             traceback.print_exc(file=sys.stderr)
             # Optionally, decide if this should break the loop or just log and continue
             # For now, let's log and continue to allow sending a 'quit' command if possible.
             pass 

    print_to_stderr("Exiting run_terminal_interface.") # Added debug log

def print_status(engine: OrchestrationEngine):
    print("--- Current Status ---")
    print(f"Engine State: {engine.state.name}")
    if engine.current_project:
        print(f"Active Project: {engine.current_project.name}")
        print(f"Project Goal: {engine.current_project.overall_goal}")
        print(f"Turns since last summary: {engine.current_project_state.gemini_turns_since_last_summary}")
        print(f"Current Summary: {engine.current_project_state.current_summary}")
        print(f"Last Agent Action: {engine.current_project_state.last_agent_action}")
        print(f"Pending User Question: {engine.pending_user_question}")
        print(f"Last Gemini Response ID: {engine.current_project_state.last_gemini_response_id}")
    else:
        print("No project selected.")
    print("--------------------")


# --- Main Execution Entry Point ---
if __name__ == "__main__":
    print_to_stderr("Entering __main__ block.") # Added debug log
    engine = None # Initialize engine to None
    
    # Add a very immediate debug print right after the import engine
    print_to_stderr("MAIN_DEBUG: Reached point immediately after import engine.") # <-- Add this line
    
    try:
        print_to_stderr("Calling ensure_app_data_scaffolding.") # Added debug log
        ensure_app_data_scaffolding()
        print_to_stderr("Finished ensure_app_data_scaffolding.") # Added debug log

        print_to_stderr("Initializing OrchestrationEngine.") # Added debug log
        engine = OrchestrationEngine()
        print_to_stderr("OrchestrationEngine initialized.") # Added debug log

        print_to_stderr("Calling run_terminal_interface.") # Added debug log
        run_terminal_interface(engine)
        print_to_stderr("run_terminal_interface finished.") # Added debug log

    except Exception as e:
        print_to_stderr(f"CRITICAL UNHANDLED EXCEPTION in __main__ block: {e}") # Added debug log
        print(f"\n--- A critical error occurred and Orchestrator Prime is shutting down: {e} ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        if engine:
             try:
                  engine.shutdown() # Attempt graceful shutdown if engine was initialized
             except Exception as sd_e:
                  print_to_stderr(f"Error during engine shutdown in exception handler: {sd_e}")
                  traceback.print_exc(file=sys.stderr)
        sys.exit(1) # Exit with error code
    
    print_to_stderr("Exiting __main__ block.") # Added debug log
    sys.exit(0) # Exit successfully if run_terminal_interface completes normally
