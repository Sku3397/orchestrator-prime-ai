#!/usr/bin/env python3
import subprocess
import os
import sys
import time
import shutil
import json
import threading
import queue
import logging
from pathlib import Path
import configparser
import re # For parsing status
from typing import Optional, Dict, Any # Added Optional, Dict, Any
import importlib # Added for reloading modules

# --- Test Configuration ---
PYTHON_EXE = sys.executable # Use the same python interpreter that runs the test script
MAIN_SCRIPT = "main.py"
TEST_DIR = Path("./temp_automated_tests").resolve()
TEST_PROJECT_NAME = "TestProj1"
TEST_PROJECT_PATH = (TEST_DIR / TEST_PROJECT_NAME).resolve()
APP_DATA_DIR = Path("./app_data").resolve()
PROJECTS_FILE = APP_DATA_DIR / "projects.json"
ORCHESTRATOR_LOG_FILE = Path("./orchestrator_prime.log").resolve()
CONFIG_FILE = Path("./config.ini").resolve()
GEMINI_COMMS_MOCK_FILE = Path("./gemini_comms_mock.py").resolve() # ADDED
GEMINI_COMMS_REAL_FILE = Path("./gemini_comms_real.py").resolve() # ADDED

# Communication constants
PROMPT_MAIN = "OP > "
PROMPT_PROJECT = f"OP (Project: {TEST_PROJECT_NAME}) > "
PROMPT_INPUT = "Gemini Needs Input > " # This might vary based on main.py exact output
DEFAULT_READ_TIMEOUT = 15 # seconds
GEMINI_INTERACTION_TIMEOUT = 180 # seconds for steps involving live API calls
MOCKED_GEMINI_TIMEOUT = 20 # seconds for mocked Gemini interactions
CURSOR_TIMEOUT_BUFFER = 10 # Extra seconds beyond configured timeout

# --- Logging Setup for Test Script ---
log_format = '%(asctime)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format)
test_logger = logging.getLogger("TestRunner")

# --- Global variable for original gemini_comms.py content ---
# ORIGINAL_GEMINI_COMMS_CONTENT: Optional[str] = None # REMOVED - No longer backing up gemini_comms.py directly

# --- Helper Functions & Class ---

def cleanup_test_environment():
    test_logger.info("Cleaning up test environment...")
    
    # Remove the entire temp_automated_tests directory
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR, ignore_errors=True)
        test_logger.info(f"Removed base test directory: {TEST_DIR}")
    
    # Remove the entire app_data directory
    if APP_DATA_DIR.exists():
        shutil.rmtree(APP_DATA_DIR, ignore_errors=True)
        test_logger.info(f"Removed app_data directory: {APP_DATA_DIR}")

    # Remove orchestrator log file
    if ORCHESTRATOR_LOG_FILE.exists():
        try:
            os.remove(ORCHESTRATOR_LOG_FILE)
            test_logger.info(f"Removed orchestrator log file: {ORCHESTRATOR_LOG_FILE}")
        except OSError as e:
            test_logger.warning(f"Could not remove {ORCHESTRATOR_LOG_FILE}: {e}")

    # Recreate necessary base directories
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    test_logger.info(f"Recreated base test directory: {TEST_DIR}")
    
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    test_logger.info(f"Recreated app_data directory: {APP_DATA_DIR}")
    # main.py's ensure_app_data_scaffolding will recreate projects.json if needed upon app start.

    # Create the base test project directory (TestProj1) for tests that assume its existence
    # Other specific test project dirs (e.g., TC18_Proj) should be created by the tests themselves.
    TEST_PROJECT_PATH.mkdir(parents=True, exist_ok=True) 
    test_logger.info(f"Ensured base test project directory exists: {TEST_PROJECT_PATH}")
    
    # Attempt to restore gemini_comms.py by deleting the mock file, so real one is used by engine
    # This should happen after all other file system cleanups to ensure it's the last step regarding this file.
    restore_gemini_comms_original() # No force_restore needed as logic changed
    test_logger.info("Cleanup complete.")

def read_output(process, stop_event, output_queue):
    """Reads stdout line by line and puts it into a queue."""
    try:
        for line in iter(process.stdout.readline, ''):
            if stop_event.is_set():
                break
            output_queue.put(line.strip())
    except Exception as e:
        test_logger.error(f"Error reading stdout: {e}") 
    finally:
        # Signal that reading is done (or stopped)
        output_queue.put(None) 

def read_stderr_output(process, stop_event, stderr_queue):
    """Reads stderr line by line and puts it into a queue or logs it."""
    # test_logger.debug("stderr_read_thread started")
    try:
        for line in iter(process.stderr.readline, ''):
            if stop_event.is_set():
                # test_logger.debug("stderr_read_thread: stop event received.")
                break
            line = line.strip()
            if line: # Only log if there is content
                # stderr_queue.put(f"STDERR: {line}") # Option 1: Put in a queue
                test_logger.info(f"SUBPROCESS_STDERR: {line}") # Option 2: Log directly
    except Exception as e:
        # test_logger.error(f"Error reading stderr: {e}", exc_info=True)
        pass # Avoid noisy errors if pipe closes, etc.
    finally:
        # test_logger.debug("stderr_read_thread finished")
        # stderr_queue.put(None) # Signal end if using queue
        pass 

class OrchestratorProcess:
    def __init__(self):
        self.process = None
        self.output_queue = queue.Queue()
        self.stderr_queue = queue.Queue() # For stderr, if chosen over direct logging
        self.stop_event = threading.Event()
        self.read_thread = None
        self.stderr_read_thread = None # Added

    def start(self):
        test_logger.info(f"Starting {MAIN_SCRIPT} process...")
        self.stop_event.clear()
        try:
            self.process = subprocess.Popen(
                [PYTHON_EXE, MAIN_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace', # Handle potential encoding errors
                bufsize=1,  # Line buffered
                cwd=Path(".").resolve() # Run from workspace root
            )
            self.read_thread = threading.Thread(target=read_output, args=(self.process, self.stop_event, self.output_queue))
            self.read_thread.daemon = True
            self.read_thread.start()

            # Start stderr reading thread
            self.stderr_read_thread = threading.Thread(target=read_stderr_output, args=(self.process, self.stop_event, self.stderr_queue))
            self.stderr_read_thread.daemon = True
            self.stderr_read_thread.start()

            test_logger.info(f"Process started (PID: {self.process.pid}). Waiting for initial prompt...")
            # Wait briefly for the initial prompt to appear
            time.sleep(0.5) # Give it a moment to start up. Reduced from 2s
            # Read initial output until prompt or timeout
            initial_output = self.read_until_prompt(expected_prompt=PROMPT_MAIN, timeout=10)
            test_logger.debug(f"Initial process output:\n{initial_output}")
            return True
        except Exception as e:
            test_logger.critical(f"Failed to start Orchestrator Prime process: {e}", exc_info=True)
            return False

    def send_command(self, command):
        if self.process and self.process.poll() is None:
            test_logger.info(f"Sending command: {command}")
            try:
                 # Ensure newline is added
                full_command = command if command.endswith('\n') else command + '\n'
                self.process.stdin.write(full_command)
                self.process.stdin.flush()
                time.sleep(0.2) # Small delay for command processing start. Reduced from 0.5s
            except (IOError, ValueError, BrokenPipeError) as e:
                 test_logger.error(f"Error writing to process stdin: {e}")
        else:
            test_logger.error("Cannot send command, process is not running.")

    def read_until_prompt(self, expected_prompt=PROMPT_MAIN, timeout=DEFAULT_READ_TIMEOUT):
        """Reads output until the expected prompt is seen or timeout occurs."""
        output_lines = []
        start_time = time.monotonic()
        test_logger.debug(f"Reading output, waiting for prompt: '{expected_prompt}'")
        while time.monotonic() - start_time < timeout:
            try:
                line = self.output_queue.get(timeout=0.5)
                if line is None: # End of stream signal
                    test_logger.warning("Output stream ended unexpectedly while waiting for prompt.")
                    break 
                test_logger.debug(f"RECV: {line}")
                output_lines.append(line)
                # Check if the last line ends with the expected prompt
                # Need rstrip because prompts might have trailing spaces sometimes
                if line.rstrip().endswith(expected_prompt.rstrip()):
                    test_logger.debug(f"Expected prompt '{expected_prompt}' found.")
                    return "\n".join(output_lines)
            except queue.Empty:
                # No output line available, check if process died
                if self.process.poll() is not None:
                     test_logger.warning("Process terminated unexpectedly while waiting for prompt.")
                     break
                continue # Continue waiting if timeout not reached
        
        test_logger.warning(f"Timeout ({timeout}s) waiting for prompt: '{expected_prompt}'")
        return "\n".join(output_lines)

    def expect_output(self, expected_substring, timeout=DEFAULT_READ_TIMEOUT):
        """Reads output until a substring is found or timeout occurs."""
        output_lines = []
        start_time = time.monotonic()
        test_logger.debug(f"Expecting output containing: '{expected_substring}'")
        while time.monotonic() - start_time < timeout:
            try:
                line = self.output_queue.get(timeout=0.5)
                if line is None: # End of stream signal
                    test_logger.warning("Output stream ended unexpectedly while waiting for expected output.")
                    break 
                test_logger.debug(f"RECV: {line}")
                output_lines.append(line)
                if expected_substring in line:
                    test_logger.debug(f"Expected substring '{expected_substring}' found.")
                    return True, "\n".join(output_lines)
            except queue.Empty:
                 if self.process.poll() is not None:
                     test_logger.warning("Process terminated unexpectedly while waiting for expected output.")
                     break
                 continue

        test_logger.warning(f"Timeout ({timeout}s) waiting for substring: '{expected_substring}'")
        return False, "\n".join(output_lines)

    def terminate(self):
        if self.process and self.process.poll() is None:
            test_logger.info(f"Terminating process (PID: {self.process.pid})...")
            self.stop_event.set() # Signal reading thread to stop
            try:
                # Try graceful termination first
                self.process.terminate()
                try:
                    stdout, stderr = self.process.communicate(timeout=5)
                    test_logger.debug(f"Process terminate stdout:\n{stdout}")
                    test_logger.debug(f"Process terminate stderr:\n{stderr}")
                except subprocess.TimeoutExpired:
                    test_logger.warning("Process did not terminate gracefully, killing.")
                    self.process.kill()
                    stdout, stderr = self.process.communicate()
                    test_logger.debug(f"Process kill stdout:\n{stdout}")
                    test_logger.debug(f"Process kill stderr:\n{stderr}")
            except Exception as e:
                test_logger.error(f"Error terminating process: {e}")
                # Ensure kill if terminate fails badly
                if self.process.poll() is None:
                    try:
                        self.process.kill()
                        test_logger.info("Process killed forcefully.")
                    except Exception as kill_e:
                        test_logger.error(f"Error killing process: {kill_e}")
        else:
            test_logger.info("Process already terminated or not started.")
            
        # Ensure reading thread is joined
        if self.read_thread and self.read_thread.is_alive():
            test_logger.debug("Waiting for output reading thread to join...")
            self.read_thread.join(timeout=2)
            if self.read_thread.is_alive():
                 test_logger.warning("Output reading thread did not join in time.")
        
        if self.stderr_read_thread and self.stderr_read_thread.is_alive(): # Added
            test_logger.debug("Waiting for stderr reading thread to join...") # Added
            self.stderr_read_thread.join(timeout=2) # Added
            if self.stderr_read_thread.is_alive(): # Added
                 test_logger.warning("Stderr reading thread did not join in time.") # Added

        self.process = None
        self.read_thread = None
        self.stderr_read_thread = None # Added
        # Reset queue and event for potential reuse
        self.output_queue = queue.Queue()
        self.stderr_queue = queue.Queue()
        self.stop_event = threading.Event()

# --- Mocking Infrastructure for gemini_comms.py ---

MOCK_GEMINI_COMMS_TEMPLATE = """
import google.api_core.exceptions # For simulating specific API errors
import logging
import time
from typing import Optional, Dict, Any, List

# These imports are assumed to be resolvable in the context where this mock
# gemini_comms.py file will be written and then imported by the test script.
# If ConfigManager or Turn are complex, the test script might need to
# ensure dummy versions are available in sys.path if the real ones aren't.
from config_manager import ConfigManager
from models import Turn

# Using __name__ will make the logger name 'gemini_comms' when this is written to gemini_comms.py
logger = logging.getLogger(__name__)

# Placeholders that will be replaced by str.format() from the test script
MOCK_DETAILS_HOLDER = '''{mock_details_placeholder}''' # Changed to triple single quotes

# Markers used by the engine to parse Gemini's special responses
GEMINI_MARKER_NEED_INPUT = "NEED_USER_INPUT:"
GEMINI_MARKER_TASK_COMPLETE = "TASK_COMPLETE"
GEMINI_MARKER_SYSTEM_ERROR = "SYSTEM_ERROR:"

# For the mock, the exact content of CURSOR_SOP_PROMPT might not be critical
# unless the mock logic itself needs to parse or use it.
# Ensure this is a clean, simple multi-line string.
CURSOR_SOP_PROMPT = '''This is a minimal placeholder for CURSOR_SOP_PROMPT.
Its full content is not essential for the mock's internal logic,
but the variable should exist if any code in the main engine
(not this mock) tries to import it from a 'gemini_comms' module.
(Ideally, the main engine imports it from its own config or constants).'''

class GeminiCommunicator:
    def __init__(self):
        self.mock_type = "{mock_type_placeholder}" # CHANGED
        # MOCK_DETAILS_HOLDER will be a string representation of a dict, or 'None'
        # We need to evaluate it safely if it's a dict string.
        details_str = MOCK_DETAILS_HOLDER
        if details_str and details_str != 'None':
            try:
                # Safely evaluate the string representation of the dictionary
                import ast
                self.details = ast.literal_eval(details_str)
            except (ValueError, SyntaxError) as e:
                logger.error(f"MOCK GeminiCommunicator: Error evaluating MOCK_DETAILS_HOLDER '{{details_str}}': {{e}}")
                self.details = {{}} # Default to empty dict on error
        else:
            self.details = {{}}


        try:
            # This will attempt to use the *actual* ConfigManager if this mock
            # is run in an environment where config_manager.py is importable.
            self.config = ConfigManager()
            self.model_name = self.config.get_gemini_model()
        except Exception as e:
            logger.error(f"MOCK GeminiCommunicator: Error loading real ConfigManager in mock: {{e}}")
            self.model_name = "mock_model_due_to_config_error"
        logger.info(f"MOCK GeminiCommunicator INSTANTIATED. Mock Type: '{{self.mock_type}}', Details: {{self.details}}")

    def get_next_step_from_gemini(self,
                                  project_goal: str,
                                  full_conversation_history: List[Turn],
                                  current_context_summary: str,
                                  max_history_turns: int,
                                  max_context_tokens: int,
                                  cursor_log_content: Optional[str],
                                  initial_project_structure_overview: Optional[str] = None
                                  ) -> Dict[str, Any]:
        logger.info(f"MOCK get_next_step_from_gemini called. Type: '{{self.mock_type}}'")
        time.sleep(0.05) # Minimal simulated delay

        # Use direct string comparisons for mock_type
        if self.mock_type == "ERROR_API_AUTH":
            logger.error("MOCK: Simulating API Auth Error (PermissionDenied)")
            raise google.api_core.exceptions.PermissionDenied("Mocked PermissionDenied: API key error.")
        elif self.mock_type == "ERROR_NON_AUTH":
            logger.error("MOCK: Simulating Non-Auth Google API Error (InvalidArgument)")
            raise google.api_core.exceptions.InvalidArgument("Mocked InvalidArgument: Non-auth API error.")
        elif self.mock_type == "NEED_INPUT":
            question = "Default mock question from Gemini?"
            if isinstance(self.details, dict) and "question" in self.details:
                question = self.details["question"]
            logger.info(f"MOCK: Returning NEED_INPUT with: {{question}}")
            return {{"status": "NEED_INPUT", "content": question}}
        elif self.mock_type == "TASK_COMPLETE":
            logger.info("MOCK: Returning TASK_COMPLETE")
            return {{"status": "COMPLETE", "content": "Mocked: Project goal achieved."}}
        elif self.mock_type == "STANDARD_INSTRUCTION":
            instruction = "Mocked standard instruction."
            if isinstance(self.details, dict) and "instruction" in self.details:
                instruction = self.details["instruction"]
            logger.info(f"MOCK: Returning STANDARD_INSTRUCTION: {{instruction}}")
            return {{"status": "INSTRUCTION", "content": instruction}}
        elif self.mock_type == "SYSTEM_ERROR_GEMINI": # For testing engine's handling of Gemini system errors
            error_message = "Simulated internal Gemini system error."
            if isinstance(self.details, dict) and "error_message" in self.details:
                error_message = self.details["error_message"]
            logger.info(f"MOCK: Returning SYSTEM_ERROR: {{error_message}}")
            # The engine expects the marker *within the content* for this specific case
            return {{"status": "INSTRUCTION", "content": f"{{GEMINI_MARKER_SYSTEM_ERROR}} {{error_message}}"}}

        # Fallback for any unhandled mock_type
        logger.warning(f"MOCK: Unknown mock type '{{self.mock_type}}'. Returning default instruction.")
        return {{"status": "INSTRUCTION", "content": "Default mock instruction (unhandled mock type)."}}

    def summarize_text(self, text_to_summarize: str, max_summary_tokens: int = 1000) -> Optional[str]:
        logger.info(f"MOCK summarize_text CALLED. Text to summarize length: {{len(text_to_summarize)}}. Max tokens: {{max_summary_tokens}}.")
        # This path is relative to where the mock gemini_comms.py will be executed from (workspace root)
        summarizer_log_file = "temp_summarizer_input.txt"
        try:
            with open(summarizer_log_file, "w", encoding='utf-8') as f:
                f.write(text_to_summarize)
            logger.info(f"MOCK summarize_text: Wrote input to {{summarizer_log_file}}")
        except Exception as e:
            logger.error(f"MOCK summarize_text: Failed to write to {{summarizer_log_file}}: {{e}}")
        
        time.sleep(0.05) # Simulate some processing
        return f"[Mocked Summary of input with length: {{len(text_to_summarize)}} chars. Max tokens: {{max_summary_tokens}}]"

"""

def apply_gemini_comms_mock(mock_type: str, details: Optional[Dict[str, Any]] = None):
    # global ORIGINAL_GEMINI_COMMS_CONTENT # REMOVED
    test_logger.info(f"Applying MOCK by writing to {GEMINI_COMMS_MOCK_FILE} with type: {mock_type}, details: {details}")

    # The original gemini_comms.py (renamed to gemini_comms_real.py) should always exist.
    # We are now writing the mock content to gemini_comms_mock.py.
    # The engine will decide whether to use gemini_comms_mock.py or gemini_comms_real.py.

    mock_content = MOCK_GEMINI_COMMS_TEMPLATE.format(
        mock_type_placeholder=mock_type,
        mock_details_placeholder=repr(details)
    )

    try:
        with open(GEMINI_COMMS_MOCK_FILE, 'w', encoding='utf-8') as f:
            f.write(mock_content)
        test_logger.info(f"Successfully wrote MOCK content to {GEMINI_COMMS_MOCK_FILE}")
        time.sleep(0.2) # Short delay for OS to flush file write before OP reload command
        
        # No need to reload modules in the test script context for this new strategy
        # The engine will handle its own module loading based on file presence.
        return True
    except Exception as e:
        test_logger.error(f"Failed to write MOCK content to {GEMINI_COMMS_MOCK_FILE}: {e}", exc_info=True)
        return False

def restore_gemini_comms_original(): # Removed force_restore parameter
    # global ORIGINAL_GEMINI_COMMS_CONTENT # REMOVED
    test_logger.info(f"Attempting to restore original comms by deleting {GEMINI_COMMS_MOCK_FILE}...")
    
    if GEMINI_COMMS_MOCK_FILE.exists():
        try:
            GEMINI_COMMS_MOCK_FILE.unlink()
            test_logger.info(f"Successfully deleted {GEMINI_COMMS_MOCK_FILE}. Engine should now use real comms.")
            time.sleep(0.2) # Short delay for OS to recognize file deletion before OP reload
            # No need to reload modules in test script context.
            return True
        except Exception as e:
            test_logger.error(f"Failed to delete {GEMINI_COMMS_MOCK_FILE} to restore original comms: {e}", exc_info=True)
            return False
    else:
        test_logger.info(f"{GEMINI_COMMS_MOCK_FILE} does not exist. Real comms should already be in effect. No action taken.")
        return True # Considered successful as the mock isn't present

# --- Helper Functions for Tests ---

def get_config_value(config_path, section, option):
    config = configparser.ConfigParser()
    config.read(config_path)
    try:
        return config.get(section, option)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return None

def set_config_value(config_path, section, option, value):
    config = configparser.ConfigParser()
    config.read(config_path)
    if not config.has_section(section):
        config.add_section(section)
    config.set(section, option, value)
    with open(config_path, 'w') as f:
        config.write(f)

# --- Individual Test Case Implementations ---

def tc1_help(op): # op is OrchestratorProcess instance
    op.send_command("help")
    output = op.read_until_prompt(PROMPT_MAIN)
    if "Available Commands:" in output and "project list" in output:
        return True, "Help command output verified."
    else:
        return False, f"Help command output missing expected content.\nOutput:\n{output}"

def tc2_project_list_empty(op):
    test_logger.info("--- Starting TC2: Project List Empty (with full cleanup) ---")
    details_log = ["TC2: Initial state"]
    passed = False
    try:
        # Ensure a completely clean environment for this test
        cleanup_test_environment()
        op.terminate() # Terminate any existing process
        if not op.start(): # Start a fresh orchestrator process
            details_log.append("TC2 FAILED: Could not start orchestrator process after cleanup.")
            raise Exception("; ".join(details_log))
        details_log.append("TC2: Orchestrator started with clean environment.")

        op.send_command("project list")
        output = op.read_until_prompt(PROMPT_MAIN)
        if "No projects found" in output or ("Available Projects:" not in output and "--- No projects found." in output) : 
            passed = True
            details_log.append("TC2 PASSED: 'No projects found' message verified.")
        else:
            passed = False
            details_log.append(f"TC2 FAILED: Expected 'No projects found', but got:\\n{output}")

    except Exception as e:
        passed = False
        details_log.append(f"TC2 EXCEPTION: {e}")
        test_logger.error(f"TC2 Exception: {e}", exc_info=True)
    finally:
        # No specific cleanup needed here as the next test requiring op will handle it
        # or a group teardown will.
        pass
    
    return passed, "; ".join(details_log)

def tc3_project_add_success(op):
    op.send_command("project add")
    # Expect prompts and send input
    output1 = op.read_until_prompt("Project Name:", timeout=7) # Increased timeout, removed trailing space
    if "Adding a new project" not in output1:
        return False, f"Did not see 'Adding a new project' prompt.\nOutput:\n{output1}"
    op.send_command(TEST_PROJECT_NAME)
    
    output2 = op.read_until_prompt("Workspace Root Path:", timeout=7) # Increased timeout, removed trailing space
    op.send_command(str(TEST_PROJECT_PATH))
    
    output3 = op.read_until_prompt("Overall Goal for the project:", timeout=7) # Increased timeout, removed trailing space
    goal_text = "Test Goal for TC3"
    op.send_command(goal_text)
    
    # Expect confirmation and return to main prompt
    output4 = op.read_until_prompt(PROMPT_MAIN, timeout=5)
    if f"Project '{TEST_PROJECT_NAME}' added successfully" not in output4:
        return False, f"Did not see project added confirmation.\nOutput:\n{output4}"
        
    # Verify projects.json
    try:
        with open(PROJECTS_FILE, 'r') as f:
            projects = json.load(f)
        if not any(p['name'] == TEST_PROJECT_NAME and p['workspace_root_path'] == str(TEST_PROJECT_PATH) for p in projects):
             return False, f"Project {TEST_PROJECT_NAME} not found or incorrect in {PROJECTS_FILE}"
    except Exception as e:
        return False, f"Error reading/checking {PROJECTS_FILE}: {e}"
        
    # Verify directories (basic check)
    if not (TEST_PROJECT_PATH / "dev_logs").exists() or not (TEST_PROJECT_PATH / "dev_instructions").exists():
        # Note: These dirs are created on SELECT, not ADD. Let's adjust the check.
        pass # Dirs created on select
        
    return True, f"Project {TEST_PROJECT_NAME} added successfully and verified in {PROJECTS_FILE}."

def tc4_project_add_invalid_path(op):
    op.send_command("project add")
    output1 = op.read_until_prompt("Project Name:", timeout=7) # Increased timeout, removed trailing space
    op.send_command("InvalidPathProject")
    
    output2 = op.read_until_prompt("Workspace Root Path:", timeout=7) # Increased timeout, removed trailing space
    invalid_path = "./path/that/does/not/existแน่นอน"
    op.send_command(invalid_path)
    
    # Expect re-prompt for path
    output3 = op.read_until_prompt("Workspace Root Path (must be an existing directory):", timeout=7) # Increased timeout, removed trailing space
    if "Invalid path." not in output3:
        return False, f"Did not receive invalid path re-prompt.\nOutput:\n{output3}"
       
    # Send valid path to exit gracefully
    op.send_command(str(TEST_PROJECT_PATH)) # Send a valid path now
    output4 = op.read_until_prompt("Overall Goal for the project:", timeout=7) # Increased timeout, removed trailing space
    op.send_command("Goal for invalid path test exit")
    output5 = op.read_until_prompt(PROMPT_MAIN, timeout=5) # Back to main prompt
    
    return True, "Invalid path during project add correctly re-prompted."

def tc5_project_add_duplicate_name(op):
    # Assumes TC3 ran successfully and TestProj1 exists
    op.send_command("project add")
    output1 = op.read_until_prompt("Project Name:", timeout=5)
    op.send_command(TEST_PROJECT_NAME) # Duplicate name
    
    output2 = op.read_until_prompt("Workspace Root Path:", timeout=5)
    op.send_command(str(TEST_PROJECT_PATH))
    
    output3 = op.read_until_prompt("Overall Goal for the project:", timeout=5)
    op.send_command("Goal for duplicate test")
    
    # Check for error message (exact wording might vary)
    output4 = op.read_until_prompt(PROMPT_MAIN, timeout=5)
    # Persistence layer should handle this, main.py might just report success from add_project if it returns existing
    # Let's check persistence log instead or refine main.py error handling
    # For now, check if no error ADDED message appears, and project count didn't increase (harder to check here)
    # Alternative: Check orchestrator log for the info message from persistence
    log_content = ""
    if ORCHESTRATOR_LOG_FILE.exists():
         with open(ORCHESTRATOR_LOG_FILE, 'r') as f:
              log_content = f.read()
    if f"Project with name '{TEST_PROJECT_NAME}' already exists" in log_content:
        return True, "Duplicate project add correctly handled (logged by persistence)."
    elif f"Project '{TEST_PROJECT_NAME}' added successfully" not in output4: # Weak check
         return True, "Duplicate project add seemed to be handled (no new success message)."
    else:
         return False, f"Duplicate project add might not have been handled correctly.\nOutput:\n{output4}\nLog:\n{log_content}"

def tc6_project_list_with_project(op):
    # Assumes TC3 ran
    op.send_command("project list")
    output = op.read_until_prompt(PROMPT_MAIN)
    if f"- {TEST_PROJECT_NAME}" in output:
        return True, "Project list correctly shows added project."
    else:
        return False, f"Project list did not contain {TEST_PROJECT_NAME}.\nOutput:\n{output}"

def tc7_project_select_success(op):
    # Assumes TC3 ran
    op.send_command(f"project select {TEST_PROJECT_NAME}")
    output = op.read_until_prompt(PROMPT_PROJECT) # Expect project prompt
    if f"Project '{TEST_PROJECT_NAME}' selected" in output:
         # Verify state dir creation
         state_dir = TEST_PROJECT_PATH / ".orchestrator_state"
         if not state_dir.is_dir():
              return False, f"Project state directory {state_dir} was not created on select."
         return True, f"Project {TEST_PROJECT_NAME} selected successfully, prompt changed, state dir exists."
    else:
         return False, f"Failed to select project {TEST_PROJECT_NAME}.\nOutput:\n{output}"

def tc8_project_select_non_existent(op):
    non_existent_name = "NoSuchProjectABC"
    op.send_command(f"project select {non_existent_name}")
    # Should remain at the current prompt (either main or project if one was selected before)
    # We need to know the previous prompt or read until timeout/specific error
    found, output = op.expect_output(f"Could not select project '{non_existent_name}'", timeout=5)
    # Read until next prompt to ensure it didn't hang
    current_prompt = PROMPT_PROJECT if TEST_PROJECT_NAME in op.read_until_prompt(expected_prompt=" > ", timeout=1) else PROMPT_MAIN
    op.read_until_prompt(current_prompt) 
    
    if found:
        return True, "Selecting non-existent project produced expected error."
    else:
        return False, f"Did not get expected error for selecting non-existent project.\nOutput:\n{output}"

def tc9_status_no_project(op):
    # Ensure no project is selected (might need to restart process or have a deselect command)
    # Easiest is to run this early before any selects.
    op.send_command("status")
    output = op.read_until_prompt(PROMPT_MAIN)
    # More robust check for TC9
    lines = output.splitlines()
    no_project_line_found = any("No project is currently active." in line for line in lines)
    prompt_returned = lines[-1].strip().endswith(PROMPT_MAIN.strip())

    if no_project_line_found and prompt_returned and "Active Project:" not in output:
        return True, "Status correctly shows no active project."
    else:
        return False, f"Status output did not indicate no active project and return to prompt correctly.\nOutput:\n{output}"

def tc10_status_project_selected_idle(op):
    # Assumes TC7 ran
    op.send_command("status")
    output = op.read_until_prompt(PROMPT_PROJECT)
    # Status should be PROJECT_SELECTED or IDLE after select, before goal
    if f"Active Project: {TEST_PROJECT_NAME}" in output and \
       ("Engine Status: PROJECT_SELECTED" in output or "Engine Status: IDLE" in output):
        return True, "Status correctly shows selected project and IDLE/PROJECT_SELECTED state."
    else:
        return False, f"Status output incorrect for selected project in idle state.\nOutput:\n{output}"

def tc11_invalid_command(op):
    tc_desc = "TC11: Invalid Command No Project Selected"
    test_logger.info(f"--- Starting {tc_desc} ---")
    passed = False
    details_log = [f"{tc_desc} initial state."]
    process_restarted_for_this_test = False

    try:
        # Ensure no project is selected. Restart OP for a clean slate.
        op.terminate()
        if not op.start():
            details_log.append(f"{tc_desc} FAILED: Could not restart Orchestrator.")
            raise Exception("; ".join(details_log))
        process_restarted_for_this_test = True
        details_log.append(f"{tc_desc}: Orchestrator restarted. No project should be selected.")

        invalid_cmd = "thisisnotavalidcommandxyz"
        op.send_command(invalid_cmd)
        
        # Expect the precise error message and then the main prompt.
        expected_error_message = f"--- Unknown command '{invalid_cmd}'. Type 'help' for available commands or 'project select <name>' to choose a project. ---"
        
        # Read until the main prompt, then check if the expected error was in the preceding lines.
        output = op.read_until_prompt(PROMPT_MAIN, timeout=5)
        
        if expected_error_message in output:
            passed = True
            details_log.append(f"{tc_desc} PASSED: Correct 'Unknown command' message received.")
        else:
            details_log.append(f"{tc_desc} FAILED: Did not receive expected 'Unknown command' message.")
            details_log.append(f"Expected to contain: '{expected_error_message}'")
            details_log.append(f"Actual Output:\\n{output}")
            passed = False

    except Exception as e:
        test_logger.error(f"{tc_desc} FAILED with exception: {e}", exc_info=True)
        if not details_log or str(e) not in details_log[-1]:
            details_log.append(f"Exception: {str(e)}")
        passed = False
    finally:
        # If this test specifically restarted the process, or if it failed,
        # ensure OP is running for subsequent tests.
        if process_restarted_for_this_test or not passed:
            if op.process and op.process.poll() is None: # If it's still running from this test's start
                pass # It's already running
            else: # It died or was terminated and needs restart
                op.terminate() # Ensure it's down first
                if not op.start():
                    test_logger.error(f"CRITICAL FAILURE in {tc_desc} finally: Could not restart OrchestratorProcess.")
                else:
                    details_log.append(f"{tc_desc}: Orchestrator process ensured running in finally block.")
        # No specific cleanup beyond ensuring OP is running for next tests.
        
    return passed, "; ".join(details_log)

def tc12_start_task_live(op: OrchestratorProcess):
    """TC12: Start a basic task, check for Gemini call and wait for cursor log. (LIVE API)"""
    test_logger.info("Starting TC12: Basic Live Gemini Turn...")
    # Assumes project is already selected (e.g., by test group setup)

    # Ensure no mocks are active for a true live test
    if not restore_gemini_comms_original():
        test_logger.warning("TC12: Failed to ensure original gemini_comms restored. Test may not be live.")
            # Allow to proceed, but it's a tainted test.
    # Call _reload_gemini_client in OP to ensure it picks up the change (or lack of mock file)
    op.send_command("_reload_gemini_client")
    current_prompt_tc12 = PROMPT_PROJECT if TEST_PROJECT_NAME in op.read_until_prompt(expected_prompt=" > ", timeout=1) else PROMPT_MAIN
    op.read_until_prompt(current_prompt_tc12, timeout=10) # Wait for OP to process reload

    # Make sure API key is valid in config
    original_api_key = get_config_value(CONFIG_FILE, "API", "gemini_api_key")
    if not original_api_key or "YOUR_API_KEY" in original_api_key or "INVALID_KEY" in original_api_key:
        test_logger.warning("TC12 SKIPPED: Valid Gemini API key not found or appears invalid in config.ini. This is a LIVE test.")
        return "SKIPPED_NO_API_KEY", "Valid API key not configured for live test."

    goal_text = "Write a simple python script that prints 'Hello World from TC12'"
    op.send_command(f"goal {goal_text}")

    # Check for Gemini call initiation
    found_gemini_call, output_gemini = op.expect_output("Calling Gemini with initial instruction", timeout=GEMINI_INTERACTION_TIMEOUT) # Live call, keep longer timeout
    if not found_gemini_call:
        found_gemini_call_alt, output_gemini_alt = op.expect_output("Calling live Gemini API", timeout=5)
        if not found_gemini_call_alt:
            return False, f"TC12 Failed: Did not see evidence of Gemini API call. Output1:\n{output_gemini}\nOutput2:\n{output_gemini_alt}"
        # output_gemini = output_gemini_alt # Use the one that matched, already done by expect_output returning it

    # Check for waiting for cursor log
    found_waiting, output_waiting = op.expect_output("Waiting for Cursor log file", timeout=GEMINI_INTERACTION_TIMEOUT)
    if not found_waiting:
        return False, f"TC12 Failed: Did not enter 'Waiting for Cursor log' state. Output after Gemini call:\n{output_waiting}"

    op.send_command("stop")
    op.read_until_prompt(PROMPT_PROJECT, timeout=10)

    test_logger.info("TC12 Passed: Successfully initiated a live Gemini task and waited for cursor.")
    return True, "Live Gemini task initiated and stop command processed."

def tc14_cursor_timeout(op):
    # Assumes TC7 ran (project selected)
    test_logger.info("Starting TC14: Cursor Timeout Test...")
    passed = False
    details_log = ["TC14 initial state"]
    process_restarted_for_this_test = False # Assume TC7 selected TestProj1

    project_name_tc14 = TEST_PROJECT_NAME # Using the default test project
    project_prompt_tc14 = f"OP (Project: {project_name_tc14}) > "

    try:
        # Ensure project is selected (should be by group setup)
        op.send_command(f"project select {project_name_tc14}")
        select_output = op.read_until_prompt(project_prompt_tc14, timeout=10)
        if f"Project '{project_name_tc14}' selected" not in select_output and f"Active project successfully set. Project: '{project_name_tc14}'" not in select_output :
            details_log.append(f"TC14 FAILED: Could not select project {project_name_tc14}. Output: {select_output}")
            raise Exception("; ".join(details_log))
        details_log.append(f"TC14: Project {project_name_tc14} selected.")

        # Apply a standard mock to prevent live API call issues before testing timeout
        mock_instruction_tc14 = "TC14 mock instruction, waiting for cursor log."
        current_mock_type_tc14_initial = "STANDARD_INSTRUCTION"
        if not apply_gemini_comms_mock(current_mock_type_tc14_initial, details={"instruction": mock_instruction_tc14}):
            details_log.append(f"TC14 FAILED: Could not apply mock for {current_mock_type_tc14_initial}.")
            raise Exception("; ".join(details_log))
        op.send_command("_reload_gemini_client") 
        op.read_until_prompt(project_prompt_tc14, timeout=10) 
        
        time.sleep(0.2) # Allow stderr logs to flush
        mock_verified_tc14_initial = False
        if ORCHESTRATOR_LOG_FILE.exists():
            log_content_tc14_initial = ORCHESTRATOR_LOG_FILE.read_text()
            expected_stderr_msg1_tc14_initial = f"SUBPROCESS_STDERR: DEBUG Engine.reinitialize_gemini_client: Detected MOCK client. Type: {current_mock_type_tc14_initial}"
            expected_stderr_msg2_tc14_initial = f"SUBPROCESS_STDERR: DEBUG Engine._load_gemini_comms_and_client: Detected MOCK client. Type: {current_mock_type_tc14_initial}"
            if expected_stderr_msg1_tc14_initial in log_content_tc14_initial or expected_stderr_msg2_tc14_initial in log_content_tc14_initial:
                mock_verified_tc14_initial = True
        if not mock_verified_tc14_initial:
            log_tail = ORCHESTRATOR_LOG_FILE.read_text()[-1000:] if ORCHESTRATOR_LOG_FILE.exists() else 'Log file not found.'
            details_log.append(f"TC14 FAILED: Engine did not confirm loading MOCK type '{current_mock_type_tc14_initial}'. Expected '{GEMINI_COMMS_MOCK_FILE.name}'. Log tail:\\n{log_tail}")
            raise Exception("; ".join(details_log))
        details_log.append(f"TC14: Applied and verified mock for {current_mock_type_tc14_initial}.")

        op.send_command("goal Trigger cursor timeout")
        found_waiting, output_wait = op.expect_output("Waiting for Cursor log", timeout=MOCKED_GEMINI_TIMEOUT) # Use mocked timeout for setup
        if not found_waiting:
            details_log.append(f"Did not enter RUNNING_WAITING_LOG state. Output: {output_wait.strip()}")
            raise Exception("; ".join(details_log))
           
        # Wait for timeout
        # Get timeout value from config
        cursor_timeout_config = get_config_value(CONFIG_FILE, "Engine", "cursor_log_timeout_seconds")
        try:
            timeout_wait_val = int(cursor_timeout_config) if cursor_timeout_config else 300 # Default from config_manager
        except ValueError:
            timeout_wait_val = 300
        
        wait_duration = timeout_wait_val + CURSOR_TIMEOUT_BUFFER 
        test_logger.info(f"TC14: Waiting {wait_duration} seconds for cursor timeout (config: {timeout_wait_val}s + buffer: {CURSOR_TIMEOUT_BUFFER}s)...")
        time.sleep(wait_duration)

        # Check for timeout error message in output (might take a moment for engine to process)
        found_error, output_error = op.expect_output("Cursor log timeout occurred", timeout=MOCKED_GEMINI_TIMEOUT) 
        if not found_error:
            # Check status as fallback
            op.send_command("status")
            status_output = op.read_until_prompt(project_prompt_tc14, timeout=10)
            if "ERROR" in status_output and "Timeout: Cursor log file" in status_output:
                details_log.append(f"Cursor timeout occurred and error state reached (verified by status). Status: {status_output.strip()}")
                # continue to recovery check
            else:
                details_log.append(f"Did not find timeout error message or status. Error Search Output: {output_error.strip()} Status Output: {status_output.strip()}")
                raise Exception("; ".join(details_log))
        else:
            details_log.append("Found 'Cursor log timeout occurred' message directly.")

        # Verify state is ERROR via status
        op.send_command("status")
        status_output_after_timeout = op.read_until_prompt(project_prompt_tc14, timeout=10)
        if "ERROR" not in status_output_after_timeout or "Timeout: Cursor log file" not in status_output_after_timeout:
            details_log.append(f"Engine state not ERROR or message incorrect after timeout. Status Output: {status_output_after_timeout.strip()}")
            raise Exception("; ".join(details_log))
        details_log.append("Engine state correctly ERROR after timeout.")

        # Verify recovery by starting a new task (mocked)
        recovery_instruction = "TC14 recovery instruction after timeout."
        current_mock_type_tc14_recovery = "STANDARD_INSTRUCTION"
        if not apply_gemini_comms_mock(current_mock_type_tc14_recovery, details={"instruction": recovery_instruction}):
            details_log.append(f"TC14 FAILED: Could not apply mock for recovery {current_mock_type_tc14_recovery}.")
            raise Exception("; ".join(details_log))
        # Need to reload client in OP for the new mock to take effect
        op.send_command("_reload_gemini_client")
        op.read_until_prompt(project_prompt_tc14, timeout=10) # Ensure OP processes the reload

        time.sleep(0.2) # Allow stderr logs to flush
        mock_verified_tc14_recovery = False
        if ORCHESTRATOR_LOG_FILE.exists():
            log_content_tc14_recovery = ORCHESTRATOR_LOG_FILE.read_text()
            expected_stderr_msg1_tc14_recovery = f"SUBPROCESS_STDERR: DEBUG Engine.reinitialize_gemini_client: Detected MOCK client. Type: {current_mock_type_tc14_recovery}"
            expected_stderr_msg2_tc14_recovery = f"SUBPROCESS_STDERR: DEBUG Engine._load_gemini_comms_and_client: Detected MOCK client. Type: {current_mock_type_tc14_recovery}"
            if expected_stderr_msg1_tc14_recovery in log_content_tc14_recovery or expected_stderr_msg2_tc14_recovery in log_content_tc14_recovery:
                mock_verified_tc14_recovery = True
        if not mock_verified_tc14_recovery:
            log_tail = ORCHESTRATOR_LOG_FILE.read_text()[-1000:] if ORCHESTRATOR_LOG_FILE.exists() else 'Log file not found.'
            details_log.append(f"TC14 FAILED: Engine did not confirm loading MOCK type '{current_mock_type_tc14_recovery}' for recovery. Expected '{GEMINI_COMMS_MOCK_FILE.name}'. Log tail:\\n{log_tail}")
            raise Exception("; ".join(details_log))
        details_log.append(f"TC14: Applied and verified mock for recovery {current_mock_type_tc14_recovery}.")

        op.send_command("goal New task after timeout")
        # Expect the mocked instruction to be in next_step.txt or in output indicating Gemini call
        found_recovery_call, output_recovery = op.expect_output(recovery_instruction, timeout=MOCKED_GEMINI_TIMEOUT)
        if not found_recovery_call:
             # Check for a more generic "Calling Gemini" or "Waiting for log" if specific instruction isn't directly in output
            found_recovery_call_generic, output_recovery_generic = op.expect_output("Waiting for Cursor log", timeout=5)
            if not found_recovery_call_generic:
                details_log.append(f"Did not successfully start new task after timeout error. Output: {output_recovery.strip()} / {output_recovery_generic.strip()}")
                raise Exception("; ".join(details_log))
        details_log.append("Successfully initiated new task after timeout error.")
           
        passed = True
        details_log.append("TC14 Passed: Cursor timeout, error state, and recovery verified.")

    except KeyboardInterrupt:
        details_log.append("Test interrupted during timeout wait.")
        passed = False
    except Exception as e:
        test_logger.error(f"TC14 FAILED with exception: {e}", exc_info=True)
        if not details_log or str(e) not in details_log[-1]:
            details_log.append(f"TC14 Exception: {e}")
        passed = False
    finally:
        restore_gemini_comms_original()
        # If process was started for this test specifically (not typical for TC14), or if it failed,
        # ensure it's reset for subsequent tests.
        if process_restarted_for_this_test or not passed: # process_restarted_for_this_test will be False here usually
            op.terminate()
            time.sleep(0.1)
            if not op.start():
                 test_logger.error("CRITICAL: Failed to restart orchestrator in TC14 finally block.")
            else:
                 details_log.append("Orchestrator (re)started in TC14 finally.")
        elif op.process and op.process.poll() is not None: # If process died and wasn't meant to restart by this test
            test_logger.warning("TC14: Process found terminated in finally block, attempting restart for subsequent tests.")
            if not op.start():
                 test_logger.error("CRITICAL: Failed to restart orchestrator (found dead) in TC14 finally block.")

    return passed, "; ".join(details_log)

def tc15_api_auth_error(op: OrchestratorProcess):
    """TC15: Test handling of API Authentication error."""
    test_logger.info("Starting TC15: API Auth Error...")
    
    original_api_key_config_val = None
    config_backup_path = CONFIG_FILE.with_suffix(".tc15.bak")
    process_restarted_for_this_test = False
    overall_passed = False
    details_log = []

    project_name_tc15_p1 = f"{TEST_PROJECT_NAME}_TC15_P1"
    project_prompt_tc15_p1 = f"OP (Project: {project_name_tc15_p1}) > "
    project_name_tc15_p2 = f"{TEST_PROJECT_NAME}_TC15_P2"
    project_prompt_tc15_p2 = f"OP (Project: {project_name_tc15_p2}) > "
    # Define project paths for TC15
    project_path_tc15_p1 = TEST_PROJECT_PATH / project_name_tc15_p1
    project_path_tc15_p2 = TEST_PROJECT_PATH / project_name_tc15_p2

    try:
        # Part 1: Test with invalid API key in config.ini
        test_logger.info("TC15 Part 1: Testing invalid API key in config.ini")
        cleanup_test_environment() # Ensures clean state, including original comms (deletes mock file)
        op.terminate() 
        if not op.start():
            details_log.append("Failed to start orchestrator for TC15 Part 1.")
            raise Exception("; ".join(details_log))
        process_restarted_for_this_test = True
        
        # Ensure project directories exist
        project_path_tc15_p1.mkdir(parents=True, exist_ok=True)
        test_logger.info(f"TC15 Part 1: Ensured project directory exists: {project_path_tc15_p1}")
        
        if CONFIG_FILE.exists():
            shutil.copy2(CONFIG_FILE, config_backup_path)
            test_logger.info(f"Backed up {CONFIG_FILE} to {config_backup_path}")
            original_api_key_config_val = get_config_value(CONFIG_FILE, "API", "gemini_api_key")
            set_config_value(CONFIG_FILE, "API", "gemini_api_key", "INVALID_KEY_FOR_TEST_TC15_CONFIG")
            test_logger.warning("Set INVALID API Key in config.ini for TC15 Part 1")
        else:
            test_logger.error(f"{CONFIG_FILE} not found. Cannot test invalid API key via config modification.")
            details_log.append("TC15 Part 1 SKIPPED: config.ini not found.")
            # Continue to Part 2 for mock-based test

        # Must restart OP for it to pick up config.ini change for API key during GeminiComms init
        op.terminate()
        if not op.start():
            details_log.append("Failed to restart orchestrator after setting invalid key for TC15 Part 1.")
            raise Exception("; ".join(details_log))
        process_restarted_for_this_test = True # Still true
        
        # op.send_command(f"project add {project_name_tc15_p1} {TEST_PROJECT_PATH / (project_name_tc15_p1)}")
        # op.read_until_prompt(PROMPT_MAIN)
        op.send_command("project add")
        op.read_until_prompt("Project Name:", timeout=7) # Increased timeout, removed trailing space
        op.send_command(project_name_tc15_p1)
        op.read_until_prompt("Workspace Root Path:", timeout=7) # Increased timeout, removed trailing space
        op.send_command(str(project_path_tc15_p1)) # Use the specific project path for TC15 P1
        op.read_until_prompt("Overall Goal for the project:", timeout=7) # Increased timeout, removed trailing space
        op.send_command("Goal for TC15 Part 1 - invalid key test")
        output_add_p1 = op.read_until_prompt(PROMPT_MAIN, timeout=5)
        if f"Project '{project_name_tc15_p1}' added successfully" not in output_add_p1:
            details_log.append(f"TC15 Part 1 FAILED: Could not add project {project_name_tc15_p1}. Output: {output_add_p1}")
            raise Exception("; ".join(details_log))
        details_log.append(f"TC15 Part 1: Project {project_name_tc15_p1} added.")

        op.send_command(f"project select {project_name_tc15_p1}")
        op.read_until_prompt(project_prompt_tc15_p1)

        op.send_command("goal This will fail due to invalid API key in config")
        time.sleep(3) # Allow time for engine to try init comms and fail
        op.send_command("status")
        status_output_part1 = op.read_until_prompt(project_prompt_tc15_p1, timeout=20)

        part1_passed = False
        if ("ERROR" in status_output_part1 and ("API Key not configured" in status_output_part1 or "Gemini model not initialized" in status_output_part1 or "API key is invalid" in status_output_part1 or "PermissionDenied" in status_output_part1 or "API key not valid" in status_output_part1)):
            test_logger.info("TC15 Part 1 Passed: Correctly handled invalid API key from config.ini.")
            details_log.append(f"TC15 Part 1 OK: Invalid API key handled. Status: {status_output_part1.strip()}")
            part1_passed = True
        elif not CONFIG_FILE.exists(): # If P1 was skipped due to no config, it didn't fail
            part1_passed = True # Mark as passed for purposes of overall test if P2 passes
        else:
            details_log.append(f"TC15 Part 1 FAILED: Did not detect error from invalid API key. Status: {status_output_part1.strip()}")
            part1_passed = False

        # Part 2: Test with mocked PermissionDenied from GeminiCommunicator
        test_logger.info("TC15 Part 2: Testing mocked PermissionDenied error from GeminiCommunicator")
        # Restore original config FIRST, then apply mock. OP restart will pick up original config, then mock applied to comms file.
        if config_backup_path.exists():
            shutil.move(str(config_backup_path), CONFIG_FILE)
            test_logger.info(f"Restored {CONFIG_FILE} from {config_backup_path} for TC15 Part 2.")
            config_backup_path.unlink(missing_ok=True)
        else: # If no backup, original_api_key_config_val might be None
            if original_api_key_config_val is not None: # Only try to set if we had a value
                 set_config_value(CONFIG_FILE, "API", "gemini_api_key", original_api_key_config_val if original_api_key_config_val else "YOUR_API_KEY_HERE") # Restore or set placeholder
                 test_logger.info("Restored original/default API key in config for TC15 Part 2.")
        
        op.terminate()
        # Ensure original comms are active (mock file deleted) before OP start for this part
        if not restore_gemini_comms_original():
            details_log.append("TC15 Part 2 WARNING: Failed to ensure mock file was deleted before OP start. Mock might not apply correctly if old mock file lingered.")
        # No _reload_gemini_client needed here as OP is about to start fresh and will load based on file presence.

        if not op.start():
            details_log.append("Failed to restart orchestrator for TC15 Part 2.")
            raise Exception("; ".join(details_log))
        process_restarted_for_this_test = True
        
        # Ensure project directory exists for part 2
        project_path_tc15_p2.mkdir(parents=True, exist_ok=True)
        test_logger.info(f"TC15 Part 2: Ensured project directory exists: {project_path_tc15_p2}")

        op.send_command("project add")
        op.read_until_prompt("Project Name:", timeout=7) 
        op.send_command(project_name_tc15_p2)
        op.read_until_prompt("Workspace Root Path:", timeout=7) 
        op.send_command(str(project_path_tc15_p2)) 
        op.read_until_prompt("Overall Goal for the project:", timeout=7) 
        op.send_command("Goal for TC15 Part 2 - mocked auth error")
        output_add_p2 = op.read_until_prompt(PROMPT_MAIN, timeout=5)
        if f"Project '{project_name_tc15_p2}' added successfully" not in output_add_p2:
            details_log.append(f"TC15 Part 2 FAILED: Could not add project {project_name_tc15_p2}. Output: {output_add_p2}")
            raise Exception("; ".join(details_log))
        details_log.append(f"TC15 Part 2: Project {project_name_tc15_p2} added.")

        op.send_command(f"project select {project_name_tc15_p2}")
        op.read_until_prompt(project_prompt_tc15_p2)

        # Apply the mock and then reload client in OP
        current_mock_type_tc15_p2 = "ERROR_API_AUTH"
        if not apply_gemini_comms_mock(current_mock_type_tc15_p2):
            details_log.append(f"TC15 Part 2 FAILED: Could not apply mock for {current_mock_type_tc15_p2}.")
            raise Exception("; ".join(details_log))
        op.send_command("_reload_gemini_client")
        op.read_until_prompt(project_prompt_tc15_p2, timeout=10) 

        time.sleep(0.2) # Allow stderr logs to flush
        mock_verified_tc15_p2 = False
        if ORCHESTRATOR_LOG_FILE.exists():
            log_content_tc15_p2 = ORCHESTRATOR_LOG_FILE.read_text()
            expected_stderr_msg1_tc15_p2 = f"SUBPROCESS_STDERR: DEBUG Engine.reinitialize_gemini_client: Detected MOCK client. Type: {current_mock_type_tc15_p2}"
            expected_stderr_msg2_tc15_p2 = f"SUBPROCESS_STDERR: DEBUG Engine._load_gemini_comms_and_client: Detected MOCK client. Type: {current_mock_type_tc15_p2}"
            if expected_stderr_msg1_tc15_p2 in log_content_tc15_p2 or expected_stderr_msg2_tc15_p2 in log_content_tc15_p2:
                mock_verified_tc15_p2 = True
        if not mock_verified_tc15_p2:
            log_tail = ORCHESTRATOR_LOG_FILE.read_text()[-1000:] if ORCHESTRATOR_LOG_FILE.exists() else 'Log file not found.'
            details_log.append(f"TC15 Part 2 FAILED: Engine did not confirm loading MOCK type '{current_mock_type_tc15_p2}'. Expected '{GEMINI_COMMS_MOCK_FILE.name}'. Log tail:\\n{log_tail}")
            raise Exception("; ".join(details_log))
        details_log.append(f"TC15 Part 2: Applied and verified {current_mock_type_tc15_p2} mock and reloaded client.")

        op.send_command("goal This will trigger mocked PermissionDenied")
        time.sleep(3) 
        op.send_command("status")
        status_output_part2 = op.read_until_prompt(project_prompt_tc15_p2, timeout=20)

        part2_passed = False
        if "ERROR" in status_output_part2 and ("PermissionDenied" in status_output_part2 or "API key not valid" in status_output_part2 or "Authentication error" in status_output_part2 or "Mocked PermissionDenied" in status_output_part2):
            test_logger.info("TC15 Part 2 Passed: Correctly handled mocked PermissionDenied.")
            details_log.append(f"TC15 Part 2 OK: Mocked PermissionDenied handled. Status: {status_output_part2.strip()}")
            part2_passed = True
        else:
            details_log.append(f"TC15 Part 2 FAILED: Did not detect mocked PermissionDenied. Status: {status_output_part2.strip()}")
            part2_passed = False
        
        overall_passed = part1_passed and part2_passed

    except Exception as e:
        test_logger.error(f"TC15 Aborted with error: {e}", exc_info=True)
        if not details_log or str(e) not in details_log[-1]:
             details_log.append(f"TC15 Exception: {e}")
        overall_passed = False
    finally:
        # Restore comms and reload client in OP if it's running
        restore_gemini_comms_original()
        if config_backup_path.exists():
            shutil.move(str(config_backup_path), CONFIG_FILE)
            test_logger.info(f"Ensured restoration of {CONFIG_FILE} from backup in TC15 finally block.")
        elif original_api_key_config_val is not None: # If backup didn't exist but we changed it
            set_config_value(CONFIG_FILE, "API", "gemini_api_key", original_api_key_config_val if original_api_key_config_val else "YOUR_API_KEY_HERE")
            test_logger.info(f"Ensured restoration of original/default API key in TC15 finally block.")
        
        if process_restarted_for_this_test or not overall_passed: 
            op.terminate()
            time.sleep(0.1)
            if not op.start():
                 test_logger.error("CRITICAL: Failed to restart orchestrator in TC15 finally block.")

    return overall_passed, "; ".join(details_log)

def tc16_google_api_other_error(op: OrchestratorProcess):
    """TC16: Test handling of a non-authentication Google API error (e.g., InvalidArgument)."""
    test_logger.info("Starting TC16: Google API Other Error (Mocked InvalidArgument)...")
    passed = False
    details = "TC16 initial state"
    process_restarted_for_this_test = False
    project_name_tc16 = f"{TEST_PROJECT_NAME}_TC16"
    project_prompt_tc16 = f"OP (Project: {project_name_tc16}) > "
    project_path_tc16 = TEST_PROJECT_PATH / project_name_tc16

    try:
        cleanup_test_environment() # Ensures clean state, including original comms (deletes mock file)
        op.terminate()
        if not op.start():
            details = "Failed to restart orchestrator for TC16."
            raise Exception(details)
        process_restarted_for_this_test = True

        # Ensure project directory exists
        project_path_tc16.mkdir(parents=True, exist_ok=True)
        test_logger.info(f"TC16: Ensured project directory exists: {project_path_tc16}")

        # op.send_command(f"project add {project_name_tc16} {TEST_PROJECT_PATH / project_name_tc16}")
        # op.read_until_prompt(PROMPT_MAIN)
        op.send_command("project add")
        op.read_until_prompt("Project Name:", timeout=7)
        op.send_command(project_name_tc16)
        op.read_until_prompt("Workspace Root Path:", timeout=7)
        op.send_command(str(project_path_tc16))
        op.read_until_prompt("Overall Goal for the project:", timeout=7)
        op.send_command("Goal for TC16 - mocked non-auth error")
        output_add_tc16 = op.read_until_prompt(PROMPT_MAIN, timeout=5)
        if f"Project '{project_name_tc16}' added successfully" not in output_add_tc16:
            details = f"TC16 Failed: Could not add project {project_name_tc16}. Output: {output_add_tc16}"
            raise Exception(details)
        details = f"TC16: Project {project_name_tc16} added."

        op.send_command(f"project select {project_name_tc16}")
        op.read_until_prompt(project_prompt_tc16)

        current_mock_type_tc16 = "ERROR_NON_AUTH"
        if not apply_gemini_comms_mock(current_mock_type_tc16):
            details = f"TC16 Failed: Could not apply Gemini comms mock for {current_mock_type_tc16}."
            raise Exception(details)
        
        op.send_command("_reload_gemini_client") # ADDED: Tell OP to reload
        op.read_until_prompt(project_prompt_tc16, timeout=10) # ADDED: Wait for prompt

        time.sleep(0.2) # Allow stderr logs to flush
        mock_verified_tc16 = False
        if ORCHESTRATOR_LOG_FILE.exists():
            log_content_tc16 = ORCHESTRATOR_LOG_FILE.read_text()
            expected_stderr_msg1_tc16 = f"SUBPROCESS_STDERR: DEBUG Engine.reinitialize_gemini_client: Detected MOCK client. Type: {current_mock_type_tc16}"
            expected_stderr_msg2_tc16 = f"SUBPROCESS_STDERR: DEBUG Engine._load_gemini_comms_and_client: Detected MOCK client. Type: {current_mock_type_tc16}"
            if expected_stderr_msg1_tc16 in log_content_tc16 or expected_stderr_msg2_tc16 in log_content_tc16:
                mock_verified_tc16 = True
        if not mock_verified_tc16:
            log_tail = ORCHESTRATOR_LOG_FILE.read_text()[-1000:] if ORCHESTRATOR_LOG_FILE.exists() else 'Log file not found.'
            details = f"TC16 FAILED: Engine did not confirm loading MOCK type '{current_mock_type_tc16}'. Expected '{GEMINI_COMMS_MOCK_FILE.name}'. Log tail:\\n{log_tail}"
            raise Exception(details)
        details = f"TC16: Applied and verified {current_mock_type_tc16} mock and reloaded client. Current details: {details}"
        test_logger.info(details) # Log the successful verification and original details

        op.send_command("goal This will trigger mocked InvalidArgument from Gemini")
        
        time.sleep(3) # Allow time for engine to process the mocked error
        op.send_command("status")
        status_output = op.read_until_prompt(project_prompt_tc16, timeout=20)
        
        if "ERROR" in status_output and ("InvalidArgument" in status_output or "Mocked InvalidArgument" in status_output or "non-auth API error" in status_output):
            test_logger.info("TC16 Passed: Correctly handled mocked Non-Auth Google API error (InvalidArgument).")
            passed = True
            details = f"TC16 OK: Mocked InvalidArgument error handled. Status: {status_output.strip()}"
        else:
            passed = False
            details = f"TC16 FAILED: Did not detect mocked InvalidArgument. Status: {status_output.strip()}"
            if ORCHESTRATOR_LOG_FILE.exists():
                with open(ORCHESTRATOR_LOG_FILE, 'r') as f_log:
                    test_logger.error(f"Orchestrator log for TC16 failure:\n{f_log.read()}")
        
    except Exception as e:
        test_logger.error(f"TC16 Aborted with error: {e}", exc_info=True)
        if "initial state" in details: details = f"TC16 Exception: {e}"
        passed = False
    finally:
        restore_gemini_comms_original()
        if process_restarted_for_this_test or not passed:
            op.terminate()
            time.sleep(0.1)
            if not op.start():
                test_logger.error("CRITICAL: Failed to restart orchestrator in TC16 finally block.")
            
    return passed, details

def tc17_stop_command(op: OrchestratorProcess):
    tc_desc = "TC17: Stop Command"
    test_logger.info(f"--- Starting {tc_desc} ---")
    passed = False
    details_log = [f"{tc_desc} initial state."]
    process_restarted_for_this_test = False # Keep track if OP is restarted *within* this test

    project_name_tc17 = TEST_PROJECT_NAME # Standard test project
    project_prompt_tc17 = f"OP (Project: {project_name_tc17}) > "

    try:
        # Attempt to select the project. If it fails, add it, then select again.
        op.send_command(f"project select {project_name_tc17}")
        select_output = op.read_until_prompt(expected_prompt=" > ", timeout=10) # Read until any prompt

        if project_prompt_tc17.strip() not in select_output.strip() or "ERROR" in select_output or "not found" in select_output:
            details_log.append(f"Project {project_name_tc17} not initially selected or error occurred. Attempting to add it. Output: {select_output.strip()}")
            
            # Ensure OP is at main prompt before adding
            if not select_output.strip().endswith(PROMPT_MAIN.strip()):
                op.send_command("quit") # Quit to get to a known state if in weird prompt
                op.terminate()
                if not op.start():
                    details_log.append("TC17 FAILED: Could not restart OP to add project.")
                    raise Exception("; ".join(details_log))
                process_restarted_for_this_test = True
            
            # Call tc3_project_add_success to add TEST_PROJECT_NAME (which is project_name_tc17)
            # This helper function expects the op process to be at the main prompt.
            add_success, add_details = tc3_project_add_success(op) # tc3 uses TEST_PROJECT_NAME
            if not add_success:
                details_log.append(f"TC17 FAILED: Could not add project {project_name_tc17} via helper. Details: {add_details}")
                raise Exception("; ".join(details_log))
            details_log.append(f"Project {project_name_tc17} added via helper.")
            
            op.send_command(f"project select {project_name_tc17}")
            select_output = op.read_until_prompt(project_prompt_tc17, timeout=10)
            if not select_output.strip().endswith(project_prompt_tc17.strip()):
                details_log.append(f"TC17 FAILED: Could not select project {project_name_tc17} even after adding. Output: {select_output.strip()}")
                raise Exception("; ".join(details_log))
        
        details_log.append(f"Project {project_name_tc17} selected successfully.")

        # Apply a standard mock to ensure Gemini interaction is predictable
        mock_instruction_tc17 = "TC17 mock instruction, task to be stopped."
        current_mock_type_tc17 = "STANDARD_INSTRUCTION"
        if not apply_gemini_comms_mock(current_mock_type_tc17, details={"instruction": mock_instruction_tc17}):
            details_log.append(f"TC17 FAILED: Could not apply mock for {current_mock_type_tc17}.")
            raise Exception("; ".join(details_log))
        
        op.send_command("_reload_gemini_client") # ADDED
        op.read_until_prompt(project_prompt_tc17, timeout=10) # ADDED

        time.sleep(0.2) # Allow stderr logs to flush
        mock_verified_tc17 = False
        if ORCHESTRATOR_LOG_FILE.exists():
            log_content_tc17 = ORCHESTRATOR_LOG_FILE.read_text()
            expected_stderr_msg1_tc17 = f"SUBPROCESS_STDERR: DEBUG Engine.reinitialize_gemini_client: Detected MOCK client. Type: {current_mock_type_tc17}"
            expected_stderr_msg2_tc17 = f"SUBPROCESS_STDERR: DEBUG Engine._load_gemini_comms_and_client: Detected MOCK client. Type: {current_mock_type_tc17}"
            if expected_stderr_msg1_tc17 in log_content_tc17 or expected_stderr_msg2_tc17 in log_content_tc17:
                mock_verified_tc17 = True
        if not mock_verified_tc17:
            log_tail = ORCHESTRATOR_LOG_FILE.read_text()[-1000:] if ORCHESTRATOR_LOG_FILE.exists() else 'Log file not found.'
            details_log.append(f"TC17 FAILED: Engine did not confirm loading MOCK type '{current_mock_type_tc17}'. Expected '{GEMINI_COMMS_MOCK_FILE.name}'. Log tail:\\n{log_tail}")
            raise Exception("; ".join(details_log))
        details_log.append(f"TC17: Applied and verified mock for {current_mock_type_tc17}.")

        op.send_command("goal Task to be stopped")
        found_waiting, output_wait = op.expect_output("Waiting for Cursor log", timeout=MOCKED_GEMINI_TIMEOUT)
        if not found_waiting:
            details_log.append(f"Did not reach RUNNING_WAITING_LOG state before stop. Output: {output_wait.strip()}")
            raise Exception("; ".join(details_log))
        details_log.append("Reached RUNNING_WAITING_LOG state.")

        op.send_command("stop")
        found_stopped, output_stop = op.expect_output("Task stopped by user", timeout=10)
        op.read_until_prompt(project_prompt_tc17) # Expect return to project prompt
        
        if not found_stopped:
            details_log.append(f"Did not see 'Task stopped by user' message. Output: {output_stop.strip()}")
            raise Exception("; ".join(details_log))
        details_log.append("'Task stopped by user' message received.")
           
        op.send_command("status")
        status_output = op.read_until_prompt(project_prompt_tc17)
        if "PROJECT_SELECTED" not in status_output and "IDLE" not in status_output: 
            details_log.append(f"Status not PROJECT_SELECTED or IDLE after stop. Status: {status_output.strip()}")
            raise Exception("; ".join(details_log))
        details_log.append("Status correctly PROJECT_SELECTED or IDLE after stop.")

        passed = True
        details_log.append(f"{tc_desc} PASSED.")

    except Exception as e:
        test_logger.error(f"{tc_desc} EXCEPTION: {e}", exc_info=True)
        if not details_log or str(e) not in details_log[-1]:
            details_log.append(f"Exception: {str(e)}")
        passed = False
    finally:
        restore_gemini_comms_original()
        if process_restarted_for_this_test or not passed: # If OP was restarted *within* this test or test failed
            op.terminate()
            time.sleep(0.1)
            if not op.start():
                 test_logger.error(f"CRITICAL FAILURE in {tc_desc} finally: Could not restart OrchestratorProcess.")
            else:
                details_log.append(f"{tc_desc}: Orchestrator (re)started in finally.")
        elif op.process and op.process.poll() is not None: # If process died unexpectedly and wasn't handled by above
            test_logger.warning(f"{tc_desc}: Process found terminated in finally block, attempting restart.")
            if not op.start():
                 test_logger.error(f"CRITICAL FAILURE in {tc_desc} finally: Could not restart dead OrchestratorProcess.")

    return passed, "; ".join(details_log)

def tc18_engine_state_reset(op: OrchestratorProcess):
    """TC18: Test engine state reset after a stop command."""
    test_logger.info("Starting TC18: Engine State Reset Test...")
    passed = False
    details_log = ["TC18 initial state"]
    process_restarted_for_this_test = False
    
    project_name_tc18 = f"{TEST_PROJECT_NAME}_TC18"
    project_path_tc18 = TEST_PROJECT_PATH.parent / project_name_tc18 # Place it alongside other test projects
    project_prompt_tc18 = f"OP (Project: {project_name_tc18}) > "
    final_mock_instruction_tc18 = "TC18 - Mocked final instruction after state reset and live call attempt"

    try:
        cleanup_test_environment() 
        op.terminate() 
        if not op.start():
            details_log.append("Failed to start orchestrator for TC18.")
            raise Exception("; ".join(details_log))
        process_restarted_for_this_test = True

        # Ensure project directory exists
        project_path_tc18.mkdir(parents=True, exist_ok=True)
        test_logger.info(f"TC18: Ensured project directory exists: {project_path_tc18}")

        # op.send_command(f"project add {project_name_tc18} {str(project_path_tc18)}")
        # op.read_until_prompt(PROMPT_MAIN)
        op.send_command("project add")
        op.read_until_prompt("Project Name:", timeout=7) # Increased timeout, removed trailing space
        op.send_command(project_name_tc18)
        op.read_until_prompt("Workspace Root Path:", timeout=7) # Increased timeout, removed trailing space
        op.send_command(str(project_path_tc18))
        op.read_until_prompt("Overall Goal for the project:", timeout=7) # Increased timeout, removed trailing space
        op.send_command("Goal for TC18 state reset test")
        output_add_tc18 = op.read_until_prompt(PROMPT_MAIN, timeout=5)
        if f"Project '{project_name_tc18}' added successfully" not in output_add_tc18:
            details_log.append(f"TC18 Failed: Could not add project {project_name_tc18}. Output: {output_add_tc18}")
            raise Exception("; ".join(details_log))
        details_log.append(f"TC18: Project {project_name_tc18} added.")

        op.send_command(f"project select {project_name_tc18}")
        op.read_until_prompt(project_prompt_tc18)

        mock_question = "Mock question for TC18: What is your favorite color?"
        current_mock_type_tc18 = "NEED_INPUT" # Store mock type
        if not apply_gemini_comms_mock(current_mock_type_tc18, details={"question": mock_question}):
            details_log.append(f"TC18 Failed: Could not apply Gemini comms mock for {current_mock_type_tc18}.")
            raise Exception("; ".join(details_log))
        
        op.send_command("_reload_gemini_client")
        # The reload command itself will return to project_prompt_tc18 if successful.
        # The engine's reaction to the NEED_INPUT mock (e.g., during a subsequent 'goal' command)
        # would then push it to PROMPT_INPUT.
        # So, here we expect project_prompt_tc18 first.
        op.read_until_prompt(project_prompt_tc18, timeout=MOCKED_GEMINI_TIMEOUT) 

        time.sleep(0.2) # Allow stderr logs to flush
        mock_verified_tc18 = False
        if ORCHESTRATOR_LOG_FILE.exists():
            log_content_tc18 = ORCHESTRATOR_LOG_FILE.read_text()
            expected_stderr_msg1_tc18 = f"SUBPROCESS_STDERR: DEBUG Engine.reinitialize_gemini_client: Detected MOCK client. Type: {current_mock_type_tc18}"
            expected_stderr_msg2_tc18 = f"SUBPROCESS_STDERR: DEBUG Engine._load_gemini_comms_and_client: Detected MOCK client. Type: {current_mock_type_tc18}"
            if expected_stderr_msg1_tc18 in log_content_tc18 or expected_stderr_msg2_tc18 in log_content_tc18:
                mock_verified_tc18 = True
        if not mock_verified_tc18:
            log_tail = ORCHESTRATOR_LOG_FILE.read_text()[-1000:] if ORCHESTRATOR_LOG_FILE.exists() else 'Log file not found.'
            details_log.append(f"TC18 FAILED: Engine did not confirm loading MOCK type '{current_mock_type_tc18}'. Expected '{GEMINI_COMMS_MOCK_FILE.name}'. Log tail:\\n{log_tail}")
            raise Exception("; ".join(details_log))
        details_log.append(f"TC18: Applied and verified mock for {current_mock_type_tc18}.")

        test_logger.info("TC18 Part 1: Triggering NEED_INPUT and checking state.")
        op.send_command("goal Trigger cursor timeout")
        found_waiting, output_wait = op.expect_output("Waiting for Cursor log", timeout=MOCKED_GEMINI_TIMEOUT)
        if not found_waiting:
            details_log.append(f"Did not enter RUNNING_WAITING_LOG state. Output: {output_wait.strip()}")
            raise Exception("; ".join(details_log))
        details_log.append("Reached RUNNING_WAITING_LOG state.")

        op.send_command("stop")
        found_stopped, output_stop = op.expect_output("Task stopped by user", timeout=10)
        op.read_until_prompt(project_prompt_tc18) # Expect return to project prompt
        
        if not found_stopped:
            details_log.append(f"Did not see 'Task stopped by user' message. Output: {output_stop.strip()}")
            raise Exception("; ".join(details_log))
        details_log.append("'Task stopped by user' message received.")
           
        op.send_command("status")
        status_output = op.read_until_prompt(project_prompt_tc18)
        if "PROJECT_SELECTED" not in status_output and "IDLE" not in status_output: 
            details_log.append(f"Status not PROJECT_SELECTED or IDLE after stop. Status: {status_output.strip()}")
            raise Exception("; ".join(details_log))
        details_log.append("Status correctly PROJECT_SELECTED or IDLE after stop.")

        passed = True
        details_log.append(f"{tc_desc} PASSED.")

    except Exception as e:
        test_logger.error(f"{tc_desc} EXCEPTION: {e}", exc_info=True)
        if not details_log or str(e) not in details_log[-1]:
            details_log.append(f"Exception: {str(e)}")
        passed = False
    finally:
        restore_gemini_comms_original()
        if process_restarted_for_this_test or not passed: # If OP was restarted *within* this test or test failed
            op.terminate()
            time.sleep(0.1)
            if not op.start():
                 test_logger.error(f"CRITICAL FAILURE in {tc_desc} finally: Could not restart OrchestratorProcess.")
            else:
                details_log.append(f"{tc_desc}: Orchestrator (re)started in finally.")
        elif op.process and op.process.poll() is not None: # If process died unexpectedly and wasn't handled by above
            test_logger.warning(f"{tc_desc}: Process found terminated in finally block, attempting restart.")
            if not op.start():
                 test_logger.error(f"CRITICAL FAILURE in {tc_desc} finally: Could not restart dead OrchestratorProcess.")

    return passed, "; ".join(details_log)

def tc19_state_persistence(op: OrchestratorProcess):
    tc_desc = "TC19: State Persistence"
    test_logger.info(f"--- Starting {tc_desc} ---")
    passed = False
    details_log = [f"{tc_desc} initial state."]
    process_restarted_for_this_test = False
    
    project_name_tc19 = f"{TEST_PROJECT_NAME}_TC19"
    project_path_tc19 = TEST_PROJECT_PATH.parent / project_name_tc19 # Place it alongside other test projects
    project_prompt_tc19 = f"OP (Project: {project_name_tc19}) > "
    final_mock_instruction_tc19 = "TC19 - Mocked final instruction after state reset and live call attempt"

    try:
        pass # Placeholder for actual test logic to be re-added if necessary
    finally:
        restore_gemini_comms_original() # Ensure mock is removed
        if project_path_tc19.exists():
            shutil.rmtree(project_path_tc19, ignore_errors=True)
            test_logger.info(f"TC19 Finally: Cleaned up project directory: {project_path_tc19}")
        
        # Ensure orchestrator is running for subsequent tests if this one restarted it or failed
        if process_restarted_for_this_test or not passed:
            op.terminate()
            time.sleep(0.1)
            if not op.start():
                 test_logger.error(f"CRITICAL FAILURE in {tc_desc} finally: Could not restart OrchestratorProcess.")
            else:
                details_log.append(f"TC19: Orchestrator process (re)started in finally block.")
        elif op.process and op.process.poll() is not None: # If process died unexpectedly
            test_logger.warning(f"{tc_desc}: Process found terminated in finally block, attempting restart.")
            if not op.start():
                 test_logger.error(f"CRITICAL FAILURE in {tc_desc} finally: Could not restart dead OrchestratorProcess.")

    return passed, "; ".join(details_log)

def tc20_context_summarization(op: OrchestratorProcess):
    tc_desc = "TC20: Context Summarization Logic"
    test_logger.info(f"--- Starting {tc_desc} ---")

    # --- Main Test Execution ---

def run_test_case(tc_num, description, test_func, op_process, *args):
    test_logger.info(f"Running Test Case {tc_num}: {description}")
    try:
        result = test_func(op_process, *args)
        test_logger.info(f"Test Case {tc_num}: {'PASS' if result[0] else 'FAIL'} - {result[1]}")
        return result
    except Exception as e:
        test_logger.error(f"Test Case {tc_num} failed with error: {e}", exc_info=True)
        return False, f"Test Case {tc_num} failed with error: {e}"

def main():
    results = {}
    orchestrator = OrchestratorProcess()

    try:
        cleanup_test_environment() # Start clean

        if not orchestrator.start():
            test_logger.critical("Orchestrator process failed to start. Aborting tests.")
            sys.exit(1)

        # --- Run Tests --- 
        # Group 1
        results["TC1"] = run_test_case(1, "Help Command", tc1_help, orchestrator)
        results["TC2"] = run_test_case(2, "Project List Empty", tc2_project_list_empty, orchestrator)
        results["TC3"] = run_test_case(3, "Project Add Success", tc3_project_add_success, orchestrator)
        results["TC4"] = run_test_case(4, "Project Add Invalid Path", tc4_project_add_invalid_path, orchestrator)
        results["TC5"] = run_test_case(5, "Project Add Duplicate Name", tc5_project_add_duplicate_name, orchestrator)
        results["TC6"] = run_test_case(6, "Project List With Project", tc6_project_list_with_project, orchestrator)
        results["TC7"] = run_test_case(7, "Project Select Success", tc7_project_select_success, orchestrator)
        results["TC8"] = run_test_case(8, "Project Select Non-existent", tc8_project_select_non_existent, orchestrator)
        
        orchestrator.terminate() # Restart for a clean slate before TC9
        orchestrator.start()
        results["TC9"] = run_test_case(9, "Status No Project", tc9_status_no_project, orchestrator)
        
        # Re-select project for TC10, TC11 as TC9 leaves no project selected
        # Need to ensure TC3 (add) and TC7 (select) equivalents are run if these tests depend on TestProj1
        # For simplicity, assume TC3 and TC7 would have created and selected TestProj1 if they were run just before.
        # This part may need adjustment if TC3/TC7 are not guaranteed to run or pass before this group.
        # As a safeguard, let's add and select it here if needed (idempotently)
        if not orchestrator.process: orchestrator.start() # Ensure started
        # Check if TestProj1 is selectable, if not, try to add it. 
        # This is a bit of a hack; ideally tests are fully independent or setup is explicit.
        # For now, let's ensure TestProj1 is added and selected for TC10 and TC11.
        run_test_case(0, "(Prereq for TC10/11) Add TestProj1", tc3_project_add_success, orchestrator)
        run_test_case(0, "(Prereq for TC10/11) Select TestProj1", tc7_project_select_success, orchestrator)
        results["TC10"] = run_test_case(10, "Status Project Selected Idle", tc10_status_project_selected_idle, orchestrator)
        results["TC11"] = run_test_case(11, "Invalid Command", tc11_invalid_command, orchestrator)
        
        # Group 2 - Live & Mocked API Interaction Tests
        test_logger.info("--- Starting Test Group: API Interactions (TC12, TC14-TC18) ---")
        orchestrator.terminate()
        cleanup_test_environment() # Full cleanup before this group
        orchestrator.start()
        run_test_case(0, "(Setup for API Group) Add TestProj1", tc3_project_add_success, orchestrator) 
        run_test_case(0, "(Setup for API Group) Select TestProj1", tc7_project_select_success, orchestrator)
        results["TC12"] = run_test_case(12, "Start Task Live Gemini (Single Turn)", tc12_start_task_live, orchestrator)
        results["TC14"] = run_test_case(14, "Cursor Timeout", tc14_cursor_timeout, orchestrator)
        results["TC15"] = run_test_case(15, "API Auth Error (Config & Mock)", tc15_api_auth_error, orchestrator)
        results["TC16"] = run_test_case(16, "Google API Other Error (Mocked)", tc16_google_api_other_error, orchestrator)
        results["TC17"] = run_test_case(17, "Stop Command", tc17_stop_command, orchestrator)
        results["TC18"] = run_test_case(18, "Engine State Reset", tc18_engine_state_reset, orchestrator)
        
        # Group 5 - Multi-Turn, Persistence, and Summarization (Corrected Grouping based on User Query)
        test_logger.info("--- Starting Test Group: Multi-Turn, Persistence & Summarization (TC13, TC19, TC20) ---")
        orchestrator.terminate()
        cleanup_test_environment()
        orchestrator.start()
        # TC13 depends on TestProj1, its internal logic should handle add/select if needed after cleanup.
        results["TC13"] = run_test_case(13, "Multi-turn Conversation", tc13_multi_turn_conversation, orchestrator)

        # TC19 manages its own full cleanup, restarts, and project creation.
        results["TC19"] = run_test_case(19, "State Persistence", tc19_state_persistence, orchestrator)
        
        # TC20 manages its own full cleanup, config changes, restarts, and project creation.
        results["TC20"] = run_test_case(20, "Context Summarization", tc20_context_summarization, orchestrator)

        # Quit gracefully
        orchestrator.send_command("quit")
        time.sleep(1) # Allow shutdown messages

    finally:
        orchestrator.terminate()
        # cleanup_test_environment() # Optional: clean up after run

    # --- Report Summary --- 
    test_logger.info("=== Test Execution Summary ===")
    passed_count = 0
    failed_count = 0
    manual_count = 0
    for tc_id, result in results.items():
        if result[0]:
            passed_count += 1
        else:
            failed_count += 1
        test_logger.info(f"  {tc_id}: {'PASS' if result[0] else 'FAIL'} - {result[1]}")
    
    total_run = passed_count + failed_count
    test_logger.info(f"PASSED: {passed_count}/{total_run} ({manual_count} manual)")
    test_logger.info(f"FAILED: {failed_count}/{total_run}")
    
    if failed_count > 0:
        sys.exit(1) # Exit with error code if tests failed
    else:
        # Final cleanup, especially gemini_comms.py
        restore_gemini_comms_original()
        sys.exit(0)

if __name__ == "__main__":
    main() 