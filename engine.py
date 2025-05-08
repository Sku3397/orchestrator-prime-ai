import os
import time
import shutil
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Callable, List, Dict, Any
import threading # For GUI updates from watchdog thread
import queue # For getting results from threaded Gemini calls
import uuid

from models import Project, ProjectState, Turn # MODIFIED
from persistence import load_project_state, save_project_state, get_project_by_id, load_projects, PersistenceError # MODIFIED
from gemini_comms import GeminiCommunicator # MODIFIED
from config_manager import ConfigManager # MODIFIED

# Watchdog is an external dependency, ensure it's handled if not available
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent
except ImportError:
    Observer = None
    FileSystemEventHandler = None
    print("WARNING (Engine): watchdog library not found. File watching will be disabled.")

# --- Status Constants ---
STATUS_IDLE = "IDLE"
STATUS_LOADING_PROJECT = "LOADING_PROJECT"
STATUS_PROJECT_LOADED = "PROJECT_LOADED"
STATUS_RUNNING_CALLING_GEMINI = "RUNNING_CALLING_GEMINI"
STATUS_RUNNING_WAITING_LOG = "RUNNING_WAITING_LOG"
STATUS_PROCESSING_LOG = "PROCESSING_LOG"
STATUS_PAUSED_WAITING_USER_INPUT = "PAUSED_WAITING_USER_INPUT"
STATUS_SUMMARIZING_CONTEXT = "SUMMARIZING_CONTEXT"
STATUS_TASK_COMPLETE = "TASK_COMPLETE"

# --- Error Status Constants ---
STATUS_ERROR = "ERROR" # Generic error
STATUS_ERROR_API_AUTH = "ERROR_API_AUTH" # Specific to API key issues
STATUS_ERROR_GEMINI_CALL = "ERROR_GEMINI_CALL" # General error from Gemini
STATUS_ERROR_GEMINI_TIMEOUT = "ERROR_GEMINI_TIMEOUT" # Gemini call timed out
STATUS_ERROR_FILE_WRITE = "ERROR_FILE_WRITE" # Cannot write instruction/state
STATUS_ERROR_FILE_READ_LOG = "ERROR_FILE_READ_LOG" # Cannot read cursor log
STATUS_ERROR_PERSISTENCE = "ERROR_PERSISTENCE" # Error loading/saving state/projects
STATUS_ERROR_WATCHER = "ERROR_WATCHER" # Watchdog failed to start/run
STATUS_ERROR_CURSOR_TIMEOUT = "ERROR_CURSOR_TIMEOUT" # Cursor log timeout

# Timeout for Gemini calls in seconds
GEMINI_CALL_TIMEOUT_SECONDS = 120 # 2 minutes
CURSOR_LOG_TIMEOUT_SECONDS = 300 # 5 minutes

class EngineState(Enum):
    IDLE = auto()
    LOADING_PROJECT = auto()
    PROJECT_SELECTED = auto() # Ready to start a task for the selected project
    RUNNING_WAITING_INITIAL_GEMINI = auto() # After user starts, before first Gemini call
    RUNNING_WAITING_LOG = auto() # Instruction sent to Cursor, waiting for cursor_step_output.txt
    RUNNING_PROCESSING_LOG = auto() # Log received, about to call Gemini
    RUNNING_CALLING_GEMINI = auto() # Calling Gemini API
    PAUSED_WAITING_USER_INPUT = auto() # Gemini requested user input
    TASK_COMPLETE = auto() # Gemini reported TASK_COMPLETE
    ERROR = auto()

class OrchestrationEngine:
    CURSOR_SOP_PROMPT_TEXT = """... (Full SOP content as defined previously) ...""" # Keep SOP text here

    def __init__(self, gui_update_callback: Optional[Callable[[str, Any], None]] = None):
        self.current_project: Optional[Project] = None
        self.current_project_state: Optional[ProjectState] = None
        self.state: EngineState = EngineState.IDLE
        self.gemini_client = GeminiCommunicator() # Might raise if config is bad
        self.config = ConfigManager()
        self.gui_update_callback = gui_update_callback # To notify GUI of changes
        self.file_observer: Optional[Observer] = None
        self._log_handler: Optional[LogFileCreatedHandler] = None # Renamed for clarity
        self.dev_logs_dir: str = ""
        self.dev_instructions_dir: str = ""
        self.last_error_message: Optional[str] = None
        self._last_critical_error: Optional[str] = None # Initialize the missing attribute

        self._engine_lock = threading.Lock() # Main lock for critical state modifications
        self._gemini_call_thread: Optional[threading.Thread] = None
        print("OrchestrationEngine initialized.")
        if self.gemini_client:
            print("GeminiCommunicator initialized successfully.")
        else:
            print("GeminiCommunicator initialization failed. Engine will be in a broken state.")
            self._last_critical_error = "GeminiCommunicator initialization failed."

    def _notify_gui(self, message_type: str, data: Any = None):
        if self.gui_update_callback:
            try:
                self.gui_update_callback(message_type, data)
            except Exception as e:
                print(f"Error in GUI callback ({message_type}): {e}")
        # Always print to console for headless debugging or if GUI callback fails
        # print(f"Engine Notification: Type={message_type}, Data={data}")

    def _set_state(self, new_state: EngineState, error_message: Optional[str] = None):
        if self.state != new_state or error_message:
            self.state = new_state
            self.last_error_message = error_message if error_message else self.last_error_message
            print(f"Engine state changed to: {self.state.name}")
            self._notify_gui("state_change", self.state.name)
            if error_message:
                self._notify_gui("error", error_message)
                print(f"Engine Error: {error_message}")
            if self.current_project_state:
                self.current_project_state.current_status = self.state.name
                # Persist state change immediately, maybe with some debouncing in a real app
                if self.current_project:
                    save_project_state(self.current_project, self.current_project_state)

    def set_active_project(self, project: Project) -> bool:
        with self._engine_lock:
            if hasattr(self, '_last_critical_error') and self._last_critical_error:
                self._notify_gui("error", self._last_critical_error)
                self._set_state(EngineState.ERROR, self._last_critical_error)
                return False

            self._set_state(EngineState.LOADING_PROJECT, f"Loading project: {project.name}...")
            self.current_project = project
            try:
                loaded_state = load_project_state(project)
            except Exception as e:
                print(f"Error loading project state for {project.name}: {e}")
                self._set_state(EngineState.ERROR, f"Persistence Error: Failed to load state for {project.name}: {e}")
                self.current_project = None # Clear project if state loading fails critically
                return False

            if loaded_state:
                self.current_project_state = loaded_state
                if self.current_project_state.current_status not in [EngineState.PAUSED_WAITING_USER_INPUT.name, EngineState.IDLE.name, EngineState.PROJECT_SELECTED.name, EngineState.TASK_COMPLETE.name] and not self.current_project_state.current_status.startswith("ERROR"):
                     self.current_project_state.current_status = EngineState.PROJECT_SELECTED.name
            else: 
                proj_id = project.id if project.id else str(uuid.uuid4()) # Assuming project might lack an ID initially
                if not project.id:
                    project.id = proj_id # Assign back if generated
                    # TODO: Need to save the updated project list if ID was generated!
                self.current_project_state = ProjectState(project_id=proj_id)
            
            try: # Ensure directories exist
                dev_logs_base = os.path.join(project.workspace_root_path, self.config.get_default_dev_logs_dir())
                dev_instructions_base = os.path.join(project.workspace_root_path, self.config.get_default_dev_instructions_dir())
                os.makedirs(dev_logs_base, exist_ok=True)
                os.makedirs(os.path.join(dev_logs_base, "processed"), exist_ok=True)
                os.makedirs(dev_instructions_base, exist_ok=True)
            except OSError as e:
                self._set_state(EngineState.ERROR, f"File Write Error: Failed to create project directories for {project.name}: {e}")
                return False

            self.dev_logs_dir = dev_logs_base
            self.dev_instructions_dir = dev_instructions_base
            
            final_state_to_set = EngineState.PROJECT_SELECTED
            
            try:
                loaded_engine_state_name = self.current_project_state.current_status
                if loaded_engine_state_name == EngineState.PAUSED_WAITING_USER_INPUT.name:
                     final_state_to_set = EngineState.PAUSED_WAITING_USER_INPUT
                elif loaded_engine_state_name == EngineState.ERROR.name:
                     final_state_to_set = EngineState.ERROR 

            except KeyError:
                print(f"Warning: Loaded project state '{self.current_project_state.current_status}' is not a valid EngineState name? Resetting to PROJECT_SELECTED.")
                final_state_to_set = EngineState.PROJECT_SELECTED
                self.current_project_state.current_status = EngineState.PROJECT_SELECTED.name
            
            self._set_state(final_state_to_set)
            
            save_project_state(self.current_project, self.current_project_state)

            self._notify_gui("project_loaded", {
                "project_name": project.name,
                "goal": project.overall_goal,
                "history": self.current_project_state.conversation_history,
                "status": self.current_project_state.current_status
            })
            print(f"Active project set to: {project.name}")

            return True

    def _start_file_watcher(self):
        if not Observer or not FileSystemEventHandler: # Watchdog not available
            self._set_state(EngineState.ERROR, "File watcher (watchdog) not available. Cannot monitor log files.")
            return
        if not self.current_project or not self.dev_logs_dir:
            self._set_state(EngineState.ERROR, "Cannot start file watcher: No active project or logs directory.")
            return

        if self.file_observer:
            self.stop_file_watcher() # Stop existing one first

        self._log_handler = LogFileCreatedHandler(self)
        self.file_observer = Observer()
        try:
            # Ensure the directory exists before watching
            os.makedirs(self.dev_logs_dir, exist_ok=True)
            self.file_observer.schedule(self._log_handler, self.dev_logs_dir, recursive=False)
            self.file_observer.start()
            print(f"File watcher started on: {self.dev_logs_dir}")
        except Exception as e:
            self._set_state(EngineState.ERROR, f"Failed to start file watcher: {e}")
            self.file_observer = None

    def stop_file_watcher(self):
        if self.file_observer:
            try:
                self.file_observer.stop()
                self.file_observer.join(timeout=5) # Wait for observer thread to finish
                print("File watcher stopped.")
            except Exception as e:
                print(f"Error stopping file watcher: {e}")
            finally:
                self.file_observer = None
                self._log_handler = None

    def _write_instruction_file(self, instruction: str):
        if not self.current_project or not self.dev_instructions_dir:
            self._set_state(EngineState.ERROR, "Cannot write instruction: No active project or instructions directory.")
            return False
        try:
            instruction_file_path = os.path.join(self.dev_instructions_dir, "next_step.txt")
            with open(instruction_file_path, 'w') as f:
                f.write(instruction)
            
            if self.current_project_state:
                self.current_project_state.last_instruction_sent = instruction
                # Add to history: Dev Manager's instruction
                self._add_to_history("gemini", instruction)
            print(f"Instruction written to: {instruction_file_path}")
            return True
        except IOError as e:
            self._set_state(EngineState.ERROR, f"Failed to write instruction file: {e}")
            return False

    def _on_log_file_created(self, log_file_path: str):
        print(f"Log file event: {log_file_path}")
        # Filter out events for non-target files or subdirectories (like 'processed')
        if os.path.basename(log_file_path) != "cursor_step_output.txt" or \
           os.path.dirname(log_file_path) != self.dev_logs_dir : 
            return

        if self.state != EngineState.RUNNING_WAITING_LOG:
            print(f"Warning: Log file created while not in RUNNING_WAITING_LOG state (current: {self.state.name}). Ignoring.")
            # Potentially move unexpected logs to processed or an 'unexpected' folder.
            return
        
        self._set_state(EngineState.RUNNING_PROCESSING_LOG)
        self.stop_file_watcher() # Stop watcher while processing to avoid duplicate events or race conditions

        try:
            time.sleep(0.5) # Small delay to ensure file is fully written
            with open(log_file_path, 'r') as f:
                log_content = f.read()
            
            self._add_to_history("cursor", log_content) # Add Cursor's log to history
            self._process_cursor_log(log_content)

            # Move processed log file
            processed_dir = os.path.join(self.dev_logs_dir, "processed")
            os.makedirs(processed_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            new_log_filename = f"cursor_step_output_{timestamp}.txt"
            shutil.move(log_file_path, os.path.join(processed_dir, new_log_filename))
            print(f"Processed log moved to: {os.path.join(processed_dir, new_log_filename)}")

        except Exception as e:
            self._set_state(EngineState.ERROR, f"Error processing log file {log_file_path}: {e}")
            # Consider if we should try to restart watcher or go to a safe state
            # If the log file caused an error, we might not want to restart the watcher immediately
            # until the issue is resolved or a timeout occurs.

    def _process_cursor_log(self, log_content: str):
        if not self.current_project or not self.current_project_state:
            self._set_state(EngineState.ERROR, "Critical error: No active project or state during log processing.")
            return

        self._set_state(EngineState.RUNNING_CALLING_GEMINI)
        self._notify_gui("status_update", "Contacting Dev Manager (Gemini)...")

        gemini_status, gemini_content = self.gemini_client.get_next_step_from_gemini(
            project_goal=self.current_project.overall_goal,
            current_context_summary=self.current_project_state.context_summary,
            full_conversation_history=self.current_project_state.conversation_history,
            max_history_turns=self.config.get_max_history_turns(),
            max_context_tokens=self.config.get_max_context_tokens(),
            cursor_log_content=log_content
        )

        if gemini_status == "INSTRUCTION":
            if self._write_instruction_file(gemini_content):
                self._set_state(EngineState.RUNNING_WAITING_LOG)
                self._start_file_watcher() # Restart watcher for the next log
            else:
                # Error already set by _write_instruction_file
                pass 
        elif gemini_status == "NEED_INPUT":
            self._set_state(EngineState.PAUSED_WAITING_USER_INPUT)
            self._add_to_history("gemini_clarification_request", gemini_content)
            self._notify_gui("user_input_needed", gemini_content)
        elif gemini_status == "COMPLETE":
            self._set_state(EngineState.TASK_COMPLETE)
            self._add_to_history("gemini", "Task marked as complete.")
            self._notify_gui("task_complete", gemini_content)
        elif gemini_status == "ERROR":
            self._set_state(EngineState.ERROR, f"Gemini API Error: {gemini_content}")
            self._add_to_history("system_error", f"Gemini API Error: {gemini_content}")

    def _add_to_history(self, sender: str, message: str):
        if self.current_project_state:
            new_turn = Turn(sender=sender, message=message) # Create Turn object
            # Timestamp is handled by Turn's default factory
            self.current_project_state.conversation_history.append(new_turn) # Append Turn object
            # Notify GUI with a dict representation for compatibility with add_message signature
            self._notify_gui("new_message", {"sender": new_turn.sender, 
                                             "message": new_turn.message, 
                                             "timestamp": new_turn.timestamp})
            # Save state after adding to history
            if self.current_project:
                 save_project_state(self.current_project, self.current_project_state)

    # --- Public Control Methods --- #

    def start_task(self, initial_user_instruction: Optional[str] = None):
        if not self.current_project or not self.current_project_state:
            self._set_state(EngineState.ERROR, "No active project selected to start.")
            return
        
        if self.state not in [EngineState.PROJECT_SELECTED, EngineState.IDLE, EngineState.TASK_COMPLETE, EngineState.ERROR]:
            self._set_state(EngineState.ERROR, f"Cannot start task from current state: {self.state.name}")
            return

        self._set_state(EngineState.RUNNING_CALLING_GEMINI)
        self._notify_gui("status_update", "Initializing task with Dev Manager (Gemini)...")

        # If there's an initial instruction from the user (e.g., refining the goal or first step)
        # We treat this as if the user just spoke, and then Gemini will use this to form the first instruction to Cursor.
        # This also means the initial_user_instruction will be part of the history for Gemini.
        if initial_user_instruction:
             self._add_to_history("user", initial_user_instruction)

        gemini_status, gemini_content = self.gemini_client.get_next_step_from_gemini(
            project_goal=self.current_project.overall_goal,
            current_context_summary=self.current_project_state.context_summary,
            full_conversation_history=self.current_project_state.conversation_history,
            max_history_turns=self.config.get_max_history_turns(),
            max_context_tokens=self.config.get_max_context_tokens(),
            cursor_log_content=None
        )

        if gemini_status == "INSTRUCTION":
            if self._write_instruction_file(gemini_content):
                self._set_state(EngineState.RUNNING_WAITING_LOG)
                self._start_file_watcher()
            # else: error already handled
        elif gemini_status == "NEED_INPUT":
            self._set_state(EngineState.PAUSED_WAITING_USER_INPUT)
            self._add_to_history("gemini_clarification_request", gemini_content)
            self._notify_gui("user_input_needed", gemini_content)
        elif gemini_status == "COMPLETE": # Unlikely on first call, but handle
            self._set_state(EngineState.TASK_COMPLETE)
            self._add_to_history("gemini", "Task marked as complete immediately.")
            self._notify_gui("task_complete", gemini_content)
        elif gemini_status == "ERROR":
            self._set_state(EngineState.ERROR, f"Gemini API Error on start: {gemini_content}")
            self._add_to_history("system_error", f"Gemini API Error on start: {gemini_content}")

    def resume_with_user_input(self, user_response: str):
        if self.state != EngineState.PAUSED_WAITING_USER_INPUT:
            self._set_state(EngineState.ERROR, "Cannot resume: Not waiting for user input.")
            return
        if not self.current_project or not self.current_project_state:
            self._set_state(EngineState.ERROR, "Critical error: No active project or state during resume.")
            return

        self._add_to_history("user", user_response)
        self._set_state(EngineState.RUNNING_CALLING_GEMINI)
        self._notify_gui("status_update", "Sending your input to Dev Manager (Gemini)...")

        gemini_status, gemini_content = self.gemini_client.get_next_step_from_gemini(
            project_goal=self.current_project.overall_goal,
            current_context_summary=self.current_project_state.context_summary,
            full_conversation_history=self.current_project_state.conversation_history,
            max_history_turns=self.config.get_max_history_turns(),
            max_context_tokens=self.config.get_max_context_tokens(),
            cursor_log_content=None
        )

        if gemini_status == "INSTRUCTION":
            if self._write_instruction_file(gemini_content):
                self._set_state(EngineState.RUNNING_WAITING_LOG)
                self._start_file_watcher()
        elif gemini_status == "NEED_INPUT": # Gemini might still need more input
            self._set_state(EngineState.PAUSED_WAITING_USER_INPUT)
            self._add_to_history("gemini_clarification_request", gemini_content)
            self._notify_gui("user_input_needed", gemini_content)
        elif gemini_status == "COMPLETE":
            self._set_state(EngineState.TASK_COMPLETE)
            self._add_to_history("gemini", "Task marked as complete.")
            self._notify_gui("task_complete", gemini_content)
        elif gemini_status == "ERROR":
            self._set_state(EngineState.ERROR, f"Gemini API Error after user input: {gemini_content}")
            self._add_to_history("system_error", f"Gemini API Error after user input: {gemini_content}")

    def pause_task(self):
        # This is a soft pause; if RUNNING_WAITING_LOG, it will continue until log is processed or explicitly stopped.
        # True pause might involve ignoring file events or stopping the watcher and then restarting carefully.
        # For now, this is more like a user request to pause, which primarily prevents new actions if IDLE/PAUSED.
        if self.state == EngineState.RUNNING_WAITING_LOG or self.state == EngineState.RUNNING_CALLING_GEMINI or self.state == EngineState.RUNNING_PROCESSING_LOG:
            # A more robust pause might need to interrupt ongoing Gemini calls or ignore incoming file events.
            # For now, we allow the current cycle (Gemini call or log processing) to complete and then go to a paused-like state.
            # This is a simplification. True interruptible pause is harder.
            # A simple approach: if watcher active, stop it. Change state. When resuming, decide if to re-issue last instruction or call Gemini.
            self.stop_file_watcher()
            self._set_state(EngineState.IDLE) # Or a new PAUSED_BY_USER state
            self._add_to_history("system", "Task processing paused by user.")
            self._notify_gui("status_update", "Task paused. File watching stopped.")
            print("Task paused by user. File watcher stopped.")
        elif self.state == EngineState.PAUSED_WAITING_USER_INPUT:
            self._notify_gui("status_update", "Already paused waiting for user input.")
        else:
            self._set_state(EngineState.IDLE) # Go to IDLE if not in an active processing state
            self._notify_gui("status_update", "Task paused/stopped.")

    def stop_task(self): # More of a reset for the current project's task
        self.stop_file_watcher()
        old_state_name = self.state.name
        self._set_state(EngineState.PROJECT_SELECTED) # Or IDLE, depending on desired UX
        self._add_to_history("system", f"Task stopped by user from state: {old_state_name}.")
        self._notify_gui("status_update", "Task stopped and reset for current project.")
        if self.current_project and self.current_project_state:
            self.current_project_state.last_instruction_sent = None
            # Optionally clear some parts of history or just keep it for context
            save_project_state(self.current_project, self.current_project_state)
        print("Task stopped by user.")

    def shutdown(self):
        print("Orchestration Engine shutting down...")
        self.stop_file_watcher()
        # Any other cleanup
        print("Orchestration Engine shutdown complete.")

# --- FileSystemEventHandler for Watchdog --- #
if FileSystemEventHandler: # Only define if watchdog is available
    class LogFileCreatedHandler(FileSystemEventHandler):
        def __init__(self, engine: 'OrchestrationEngine'):
            self.engine = engine

        def on_created(self, event):
            if not event.is_directory and os.path.basename(event.src_path) == "cursor_step_output.txt":
                # Check if the event is for the expected log file in the active project's dev_logs directory
                if self.engine.current_project and self.engine.current_project_state:
                    expected_dir = os.path.join(self.engine.current_project.workspace_root_path, self.engine.config.get_default_dev_logs_dir())
                    if os.path.dirname(event.src_path) == expected_dir:
                        print(f"Engine Watcher: Detected log file: {event.src_path}")
                        self.engine._cancel_cursor_timeout()
                        time.sleep(0.5) 
                        self.engine._handle_log_file_created(event.src_path)
                    else:
                        print(f"Engine Watcher: Ignored log file in unexpected directory: {event.src_path}")
                else:
                    print(f"Engine Watcher: Ignored log file, no active project: {event.src_path}")
            # else:
                # print(f"Engine Watcher: Ignored event: {event.src_path} (is_directory: {event.is_directory}, name: {os.path.basename(event.src_path)})")
else:
    LogFileCreatedHandler = None # Placeholder if watchdog not imported

# Example Usage (illustrative, assumes GUI/callbacks are handled elsewhere)
if __name__ == '__main__':
    print("Starting OrchestrationEngine example...")
    # Dummy callback for testing
    def dummy_gui_callback(msg_type, data):
        print(f"GUI Callback: {msg_type} - {data}")

    engine = OrchestrationEngine(gui_update_callback=dummy_gui_callback)

    # 1. Create a dummy project for testing (persistence would normally handle this)
    mock_project_dir = "./mock_orchestrator_workspace"
    if not os.path.exists(mock_project_dir):
        os.makedirs(mock_project_dir)
    
    from .persistence import add_project # Use the actual add_project
    # Clear old test projects if any
    if os.path.exists("app_data/projects.json"):
        with open("app_data/projects.json", "w") as f: json.dump([],f)

    test_project = add_project(
        name="EngineTestProject", 
        workspace_root_path=mock_project_dir, 
        overall_goal="Test the orchestration engine components."
    )

    if not test_project:
        print("Failed to create test project for engine example. Exiting.")
        exit()

    print(f"Test project created: {test_project.name}")

    # 2. Set active project
    if engine.set_active_project(test_project):
        print(f"Successfully set active project: {engine.current_project.name}")
        print(f"Initial engine state: {engine.state.name}")

        # 3. Simulate starting a task
        engine.start_task("Please write a Python script that prints numbers 1 to 5.")
        print(f"Engine state after start_task: {engine.state.name}")

        # 4. Manually simulate Cursor creating the log file (since watcher runs in a thread)
        # In a real scenario, the watcher would pick this up.
        if engine.state == EngineState.RUNNING_WAITING_LOG:
            print("Engine is waiting for log. Simulating log creation...")
            time.sleep(2) # Give time for watcher to start if it were real
            log_file = os.path.join(engine.dev_logs_dir, "cursor_step_output.txt")
            with open(log_file, "w") as f:
                f.write("SUCCESS: Created script numbers.py. It prints numbers 1 to 5.")
            print(f"Simulated log file created: {log_file}")
            
            # Manually trigger the handler for this example if watcher is not running or for direct test
            if LogFileCreatedHandler: # Check if class is defined
                engine._process_cursor_log(log_file) # Manually call for testing flow
            else:
                print("Watchdog not available, manual trigger of _process_cursor_log needed for full test.")

            # Wait for Gemini call and next instruction (simulated)
            time.sleep(5) # Simulate Gemini processing time
            print(f"Engine state after simulated log processing: {engine.state.name}")
            if engine.current_project_state and engine.current_project_state.last_instruction_sent:
                print(f"Next instruction from Gemini (simulated): {engine.current_project_state.last_instruction_sent}")
            
            # Simulate another log if needed
            if engine.state == EngineState.RUNNING_WAITING_LOG and engine.current_project_state.last_instruction_sent != "Task marked as complete.":
                print("Simulating second log file for TASK_COMPLETE...")
                second_log_file = os.path.join(engine.dev_logs_dir, "cursor_step_output.txt")
                with open(second_log_file, "w") as f:
                    f.write("SUCCESS: Task fully completed as per instructions.")
                if LogFileCreatedHandler:
                    engine._process_cursor_log(second_log_file)
                time.sleep(5)
                print(f"Engine state after second log: {engine.state.name}")


        else:
            print(f"Engine not in RUNNING_WAITING_LOG state after start_task. Current state: {engine.state.name}. Error: {engine.last_error_message}")

    else:
        print(f"Failed to set active project. Error: {engine.last_error_message}")

    engine.shutdown()
    # Clean up mock directory
    # shutil.rmtree(mock_project_dir)
    # print(f"Cleaned up {mock_project_dir}")
    print("OrchestrationEngine example finished.") 
    engine.shutdown()
    # Clean up mock directory
    # shutil.rmtree(mock_project_dir)
    # print(f"Cleaned up {mock_project_dir}")
    print("OrchestrationEngine example finished.") 
    # print(f"Cleaned up {mock_project_dir}")
    print("OrchestrationEngine example finished.") 
    # print(f"Cleaned up {mock_project_dir}")
    print("OrchestrationEngine example finished.") 
    # print(f"Cleaned up {mock_project_dir}")
    print("OrchestrationEngine example finished.") 