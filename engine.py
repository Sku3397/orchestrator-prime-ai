import os
import time
import shutil
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Callable, List, Dict, Any
import threading # For GUI updates from watchdog thread
import queue # For getting results from threaded Gemini calls
import uuid
import traceback # Added for printing stack

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
CURSOR_LOG_TIMEOUT_SECONDS = 300 # 5 minutes # RESTORED AFTER TEST 8

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
        self._cursor_timeout_timer: Optional[threading.Timer] = None # Added for timeout

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
        # If error_message is provided but new_state isn't ERROR, it's contextual info for the state.
        # If new_state is ERROR, error_message should ideally be present.
        if self.state != new_state or error_message: # Log if state changes or if there's a message for the current state
            self.state = new_state
            if new_state == EngineState.ERROR:
                self.last_error_message = error_message if error_message else "Unknown error" # Ensure error message exists
                print(f"Engine state changed to: {self.state.name} - Error: {self.last_error_message}")
                self._notify_gui("state_change", self.state.name) # Notify state change first
                self._notify_gui("error", self.last_error_message) # Then notify the error details
            else:
                # For non-error states, an error_message is just a status detail
                status_detail = f" - Detail: {error_message}" if error_message else ""
                print(f"Engine state changed to: {self.state.name}{status_detail}")
                self._notify_gui("state_change", self.state.name)
                if error_message: # If there was a detail message for a non-error state, send as status update
                    self._notify_gui("status_update", error_message)
            
            if self.current_project_state:
                self.current_project_state.current_status = self.state.name
                if self.current_project:
                    save_project_state(self.current_project, self.current_project_state)

    def set_active_project(self, project: Project) -> bool:
        with self._engine_lock:
            if hasattr(self, '_last_critical_error') and self._last_critical_error:
                # self._notify_gui("error", self._last_critical_error) # _set_state will handle this
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
                     # If loaded state is ERROR, we need the accompanying message.
                     # This assumes last_error_message in ProjectState would be good.
                     # For now, _set_state for ERROR will use a generic if not passed one.
                     # This part might need refinement if we want to preserve specific old error messages.
                     final_state_to_set = EngineState.ERROR 
                     # We need to fetch the actual error message that caused this ERROR state if possible
                     # For now, if we land here, set_active_project will use a generic "Error state loaded"
                     # Or rely on self.last_error_message if it was set by a previous operation in this session.
                     # This is tricky. Let's assume for now that if a project *loads* into an ERROR state,
                     # it's an error with the project loading itself, or it should be cleared.
                     # The `if not self.current_project_state.current_status.startswith("ERROR")`
                     # above should prevent an old generic error state from persisting without reason.

            except KeyError: # This was a bug, should be AttributeError if .name is accessed on non-Enum
                 print(f"Warning: Loaded project state '{self.current_project_state.current_status}' is not a valid EngineState object. Resetting to PROJECT_SELECTED.")
                 final_state_to_set = EngineState.PROJECT_SELECTED
                 self.current_project_state.current_status = EngineState.PROJECT_SELECTED.name # Store the name
            
            # Pass the specific error message if we are setting the state to ERROR
            if final_state_to_set == EngineState.ERROR:
                # If we loaded an error state, we need to find what the error message was.
                # This is not stored directly in a way that final_state_to_set can use.
                # For now, let's clear such an error state upon loading.
                # If a project's state.json says ERROR, it's better to load it as PROJECT_SELECTED
                # and let new operations determine if there's a new error.
                # The earlier check: `and not self.current_project_state.current_status.startswith("ERROR")`
                # was intended to reset stale error states. Let's refine it.
                if self.current_project_state.current_status == EngineState.ERROR.name:
                    print(f"Project {project.name} loaded with a previous ERROR status. Resetting to PROJECT_SELECTED.")
                    final_state_to_set = EngineState.PROJECT_SELECTED
                    self.current_project_state.current_status = EngineState.PROJECT_SELECTED.name

                self._set_state(final_state_to_set) # Error message will be handled by _set_state if it's an actual error state
            else:
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

        # Ensure any existing observer is fully stopped before creating a new one
        if self.file_observer and self.file_observer.is_alive(): # Check if it's alive
            print("DEBUG_ENGINE: Attempting to stop existing file watcher before starting a new one...")
            self.stop_file_watcher() # This will join and nullify

        # Defensive check if stop_file_watcher failed to nullify or wasn't called correctly
        if self.file_observer:
            print("WARNING_ENGINE: file_observer was not None before creating a new one. Explicitly nullifying.")
            self.file_observer = None # Ensure it's None

        self._log_handler = LogFileCreatedHandler(self)
        self.file_observer = Observer() # Create a new observer instance
        try:
            os.makedirs(self.dev_logs_dir, exist_ok=True)
            self.file_observer.schedule(self._log_handler, self.dev_logs_dir, recursive=False)
            self.file_observer.start()
            self._start_cursor_timeout()
            print(f"File watcher started on: {self.dev_logs_dir}")
        except Exception as e: # More specific exceptions could be caught, e.g., for read-only FS
            self._set_state(EngineState.ERROR, f"Failed to start file watcher: {e}")
            self.file_observer = None # Ensure nullification on error

    def stop_file_watcher(self):
        observer_to_stop = self.file_observer # Capture current observer
        if observer_to_stop: # Check if there is an observer instance
            self.file_observer = None # Nullify immediately to prevent re-entry issues or use by other parts
            if observer_to_stop.is_alive():
                try:
                    print("DEBUG_ENGINE: Stopping file watcher observer thread...")
                    observer_to_stop.stop()
                    # observer_to_stop.join(timeout=1) # Shorter timeout for join
                except Exception as e: # Catch errors during stop (e.g. if thread already stopped)
                    print(f"Warning_ENGINE: Error during observer.stop(): {e}")
                
                try:
                    # Wait for the thread to terminate with a timeout
                    # It's crucial that join is called on the thread that is running.
                    # The observer object itself is not the thread, but manages it.
                    # Observer.join() is the correct method.
                    observer_to_stop.join(timeout=2) # Increased timeout slightly
                    if observer_to_stop.is_alive():
                        print("WARNING_ENGINE: File watcher observer thread did not terminate in time after join.")
                    else:
                        print("DEBUG_ENGINE: File watcher observer thread successfully joined.")
                except Exception as e: # Catch errors during join (e.g. RuntimeError if trying to join current thread - though unlikely here)
                    print(f"Warning_ENGINE: Error during observer.join(): {e}")
            else:
                print("DEBUG_ENGINE: File watcher observer was not alive when stop_file_watcher was called.")
            
            self._log_handler = None # Clear handler as well
            print("File watcher stop sequence complete.")
        # else:
            # print("DEBUG_ENGINE: stop_file_watcher called but no observer instance to stop.")

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

        response_dict = self.gemini_client.get_next_step_from_gemini(
            project_goal=self.current_project.overall_goal,
            current_context_summary=self.current_project_state.context_summary,
            full_conversation_history=self.current_project_state.conversation_history,
            max_history_turns=self.config.get_max_history_turns(),
            max_context_tokens=self.config.get_max_context_tokens(),
            cursor_log_content=log_content
        )
        gemini_status = response_dict.get("status")
        gemini_content = response_dict.get("content")

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
            # Notify GUI *before* saving state, just in case saving blocks briefly
            self._notify_gui("new_message", {"sender": new_turn.sender, 
                                             "message": new_turn.message, 
                                             "timestamp": new_turn.timestamp})
            # Save state after adding to history
            if self.current_project:
                 save_project_state(self.current_project, self.current_project_state)

    # --- Summarization Logic --- #
    def _check_and_run_summarization(self):
        if not self.current_project or not self.current_project_state or not self.gemini_client:
            return # Cannot summarize without project/state/client
            
        interval = self.config.get_summarization_interval()
        history = self.current_project_state.conversation_history
        
        # Trigger summarization if interval is positive and met
        # Also summarize if context_summary is None but history exists (e.g., first load after crash)
        should_summarize = (interval > 0 and len(history) > 0 and len(history) % interval == 0) or \
                           (self.current_project_state.context_summary is None and len(history) > 1) # Summarize if summary missing but history exists

        if should_summarize:
            print(f"** Engine: Checking summarization need. History length: {len(history)}, Interval: {interval}. Triggered: {should_summarize} **")
            # Simple summarization: Join last 'interval' turns (or all if summary was missing)
            turns_to_summarize_count = interval if (interval > 0 and len(history) % interval == 0) else len(history)
            start_index = max(0, len(history) - turns_to_summarize_count)
            
            text_parts = []
            if self.current_project_state.context_summary:
                 text_parts.append(f"Previous Summary:\n{self.current_project_state.context_summary}\n")
            text_parts.append("New History Since Last Summary:")
            for turn in history[start_index:]:
                 text_parts.append(f"[{turn.sender} @ {turn.timestamp}]: {turn.message}")
            
            text_to_summarize = "\n".join(text_parts)
            
            print(f"** Engine: Attempting to summarize context ({len(text_to_summarize)} chars)... **")
            # Consider running summarization in a separate thread if it becomes time-consuming
            try:
                # Use a reasonable token limit for summary, maybe from config?
                summary = self.gemini_client.summarize_text(text_to_summarize, max_summary_tokens=500) 
                if summary:
                    print("** Engine: Summarization successful. Updating project state. **")
                    self.current_project_state.context_summary = summary
                    # We don't add this to the main visible history, it's meta-context
                    # self._add_to_history("SYSTEM", "Conversation context summarized.") # Avoid cluttering main history
                    save_project_state(self.current_project, self.current_project_state)
                else:
                    print("** Engine Warning: Summarization call returned None or empty string. **")
            except Exception as e:
                 print(f"** Engine Error: Summarization failed: {e} **")

    # --- Public Control Methods --- #

    def start_task(self, initial_user_instruction: Optional[str] = None):
        with self._engine_lock:
            if hasattr(self, '_last_critical_error') and self._last_critical_error:
                self._set_state(EngineState.ERROR, self._last_critical_error)
                return

            if not self.current_project or not self.current_project_state:
                self._set_state(EngineState.ERROR, "Cannot start task: No project selected or state not loaded.")
                return

            # Check for summarization BEFORE calling Gemini
            self._check_and_run_summarization() 

            # Determine if this is the first meaningful interaction for the current task/project state
            is_first_interaction = not self.current_project_state.conversation_history
            if not is_first_interaction and self.current_project_state.conversation_history[-1].sender == "USER" and self.current_project_state.conversation_history[-1].message == initial_user_instruction:
                # This handles the case where start_task is called immediately after setting user input that forms part of history
                # Check if the very last message IS the initial_user_instruction from USER, then it's still effectively a first Gemini call for this.
                 if len(self.current_project_state.conversation_history) == 1: # Only one user message means Gemini hasn't spoken yet.
                     is_first_interaction = True

            initial_project_structure_overview_str: Optional[str] = None
            if is_first_interaction:
                print(f"DEBUG_ENGINE: First interaction for project '{self.current_project.name}'. Generating structure overview.")
                try:
                    workspace_path = self.current_project.workspace_root_path
                    print(f"DEBUG_ENGINE: Workspace path for structure overview: {workspace_path}")
                    if os.path.isdir(workspace_path):
                        entries = os.listdir(workspace_path)
                        files = [e for e in entries if os.path.isfile(os.path.join(workspace_path, e))][:5] # Limit to 5 files
                        dirs = [e for e in entries if os.path.isdir(os.path.join(workspace_path, e))][:5]   # Limit to 5 dirs
                        
                        structure_parts = []
                        if files:
                            structure_parts.append(f"Files: {', '.join(files)}")
                        if dirs:
                            structure_parts.append(f"Directories: {', '.join(dirs)}")
                        
                        if structure_parts:
                            initial_project_structure_overview_str = f"Project root (\"{workspace_path}\") top-level structure: {'; '.join(structure_parts)}."
                        else:
                            initial_project_structure_overview_str = f"Project root (\"{workspace_path}\") is empty or structure could not be determined."
                        print(f"DEBUG_ENGINE: Generated structure overview: {initial_project_structure_overview_str}")
                    else:
                        print(f"DEBUG_ENGINE: Workspace path {workspace_path} is not a valid directory.")
                        initial_project_structure_overview_str = f"Project root (\"{workspace_path}\") is not a valid directory."

                except Exception as e:
                    print(f"ERROR_ENGINE: Failed to generate project structure overview: {e}")
                    # Optionally, pass this error as part of the overview string or handle differently
                    initial_project_structure_overview_str = f"Error generating project structure: {e}"

            if self.state not in [EngineState.IDLE, EngineState.PROJECT_SELECTED, EngineState.TASK_COMPLETE, EngineState.ERROR]:
                if self.state == EngineState.PAUSED_WAITING_USER_INPUT and initial_user_instruction:
                    # This is actually a resume_with_user_input scenario called via start_task logic in GUI
                    print(f"Engine: Resuming task due to start_task call while PAUSED_WAITING_USER_INPUT with instruction: {initial_user_instruction}")
                    self.resume_with_user_input(initial_user_instruction)
                    return 
                else:
                    print(f"Engine state is {self.state.name}, cannot start a new task flow now.")
                    self._notify_gui("status_update", f"Engine is busy ({self.state.name}). Cannot start new task now.")
                    return
            
            self._set_state(EngineState.RUNNING_WAITING_INITIAL_GEMINI, "Preparing initial Gemini call.")
            
            if initial_user_instruction:
                self._add_to_history("USER", initial_user_instruction)
            elif not self.current_project_state.conversation_history: # First run, no instruction, use goal
                self._add_to_history("SYSTEM", "Task started based on project goal.")

            # Prepare for Gemini call
            current_summary = self.current_project_state.context_summary
            full_history = self.current_project_state.conversation_history
            goal = self.current_project.overall_goal

            # Asynchronous Gemini Call
            if self._gemini_call_thread and self._gemini_call_thread.is_alive():
                self._set_state(EngineState.ERROR, "Gemini call already in progress. New task aborted.")
                return

            # Threading setup for Gemini call
            result_queue = queue.Queue()
            self._gemini_call_thread = threading.Thread(
                target=self._call_gemini_in_thread,
                args=(goal, full_history, current_summary, initial_project_structure_overview_str, None, result_queue) # Pass structure overview
            )
            self._gemini_call_thread.start()

            # Non-blocking wait for result (or handle via callback if GUI exists)
            # For now, let's assume direct processing or a more complex async handler would be here.
            # This example will be simplified for direct testability.
            # The actual Gemini result processing and state transition will happen in _process_gemini_response.
            print("DEBUG_ENGINE: Thread for _call_gemini_in_thread started. Waiting for queue...")
            try:
                gemini_response_data = result_queue.get(timeout=GEMINI_CALL_TIMEOUT_SECONDS)
                print(f"DEBUG_ENGINE: Gemini response received from queue: {gemini_response_data}") # Print response for testing
                self._process_gemini_response(gemini_response_data)
            except queue.Empty:
                self._set_state(EngineState.ERROR, "Gemini call timed out.")
                print("ERROR_ENGINE: Gemini call timed out waiting for response from queue.")
            except Exception as e:
                self._set_state(EngineState.ERROR, f"Error processing Gemini response from queue: {e}")
                print(f"ERROR_ENGINE: Exception processing Gemini response: {traceback.format_exc()}")

    def _call_gemini_in_thread(self, project_goal, full_history, current_summary, initial_structure_overview, cursor_log_content, q):
        # This method runs in a separate thread
        self._set_state(EngineState.RUNNING_CALLING_GEMINI, "Contacting Gemini...")
        try:
            max_hist = self.config.get_max_history_turns()
            max_tokens = self.config.get_max_context_tokens()
            response = self.gemini_client.get_next_step_from_gemini(
                project_goal=project_goal,
                full_conversation_history=full_history,
                current_context_summary=current_summary,
                max_history_turns=max_hist,
                max_context_tokens=max_tokens,
                cursor_log_content=cursor_log_content,
                initial_project_structure_overview=initial_structure_overview # Pass here
            )
            q.put(response)
        except Exception as e:
            print(f"ERROR_ENGINE: Exception in _call_gemini_in_thread: {traceback.format_exc()}")
            q.put({"status": "ERROR", "content": f"Exception during Gemini call: {e}"})

    def _process_gemini_response(self, response_data: Dict[str, Any]):
        if not self.current_project or not self.current_project_state:
            self._set_state(EngineState.ERROR, "Critical error: No active project or state during Gemini response processing.")
            return

        gemini_status = response_data.get("status")
        gemini_content = response_data.get("content")

        if gemini_status == "INSTRUCTION":
            if self._write_instruction_file(gemini_content):
                self._set_state(EngineState.RUNNING_WAITING_LOG)
                self._start_file_watcher()
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

    def resume_with_user_input(self, user_response: str):
        with self._engine_lock:
            if not self.current_project or not self.current_project_state:
                self._set_state(EngineState.ERROR, "Cannot resume: No project selected or state not loaded.")
                return

            if self.state != EngineState.PAUSED_WAITING_USER_INPUT:
                self._set_state(EngineState.ERROR, f"Cannot resume: Engine not in PAUSED_WAITING_USER_INPUT state (is {self.state.name}).")
                return
            
            self._add_to_history("USER", user_response)

            # Check for summarization BEFORE calling Gemini
            self._check_and_run_summarization() 

            # Prepare for Gemini call
            current_summary = self.current_project_state.context_summary
            full_history = self.current_project_state.conversation_history
            goal = self.current_project.overall_goal

            if self._gemini_call_thread and self._gemini_call_thread.is_alive():
                self._set_state(EngineState.ERROR, "Gemini call already in progress during resume. Aborted.")
                return

            result_queue = queue.Queue()
            self._gemini_call_thread = threading.Thread(
                target=self._call_gemini_in_thread,
                # NOTE: Resume doesn't have 'initial_structure_overview' conceptually.
                # It also doesn't typically use 'cursor_log_content' directly, that's processed *before* resume.
                args=(goal, full_history, current_summary, None, None, result_queue) 
            )
            self._gemini_call_thread.start()

            print("DEBUG_ENGINE: Thread for _call_gemini_in_thread started from resume. Waiting for queue...")
            try:
                gemini_response_data = result_queue.get(timeout=GEMINI_CALL_TIMEOUT_SECONDS)
                print(f"DEBUG_ENGINE: Gemini response received from resume queue: {gemini_response_data}")
                self._process_gemini_response(gemini_response_data)
            except queue.Empty:
                self._set_state(EngineState.ERROR, "Gemini call timed out during resume.")
                print("ERROR_ENGINE: Gemini call timed out waiting for response from resume queue.")
            except Exception as e:
                self._set_state(EngineState.ERROR, f"Error processing Gemini response from resume queue: {e}")
                print(f"ERROR_ENGINE: Exception processing Gemini response from resume: {traceback.format_exc()}")

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
        print(f"DEBUG_ENGINE: stop_task invoked from state: {self.state.name}") # Added debug print
        traceback.print_stack() # Added stack trace
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

    def _start_cursor_timeout(self): # Added
        self._cancel_cursor_timeout() # Cancel any existing timer first
        print(f"DEBUG_ENGINE: Starting cursor log timeout ({CURSOR_LOG_TIMEOUT_SECONDS}s)...")
        self._cursor_timeout_timer = threading.Timer(
            CURSOR_LOG_TIMEOUT_SECONDS,
            self._handle_cursor_timeout
        )
        self._cursor_timeout_timer.daemon = True # Allow program to exit even if timer is pending
        self._cursor_timeout_timer.start()

    def _cancel_cursor_timeout(self):
        if self._cursor_timeout_timer:
            print("DEBUG_ENGINE: Cancelling cursor log timeout timer.")
            self._cursor_timeout_timer.cancel()
            self._cursor_timeout_timer = None
        # print("DEBUG_ENGINE: _cancel_cursor_timeout() called.") # Original placeholder message
        # pass # Original placeholder

    def _handle_cursor_timeout(self): # Added
        with self._engine_lock:
            if self.state == EngineState.RUNNING_WAITING_LOG:
                print("ERROR: Cursor log timeout occurred!")
                self.stop_file_watcher() # Stop watcher if timeout occurs
                self._set_state(EngineState.ERROR, f"Timeout: Cursor log file did not appear within {CURSOR_LOG_TIMEOUT_SECONDS} seconds.")
                # Notify GUI about the timeout specifically?
                # self._notify_gui("cursor_timeout", None)
            else:
                # Timer fired, but we are no longer waiting for the log (e.g., task stopped, log arrived)
                print(f"DEBUG_ENGINE: Cursor timeout timer fired, but state is {self.state.name}. No action needed.")
            self._cursor_timeout_timer = None # Timer has done its job

    def shutdown(self):
        print("Orchestration Engine shutting down...")
        self.stop_file_watcher()
        # Any other cleanup
        print("Orchestration Engine shutdown complete.")

    def get_project_path(self):
        return self.current_project.path if self.current_project else None

    def get_current_state(self):
        return self.state

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
                        self.engine._on_log_file_created(event.src_path)
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