import os
import time
import shutil
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Callable, List, Dict, Any
import threading
import queue
import uuid
import traceback
import json
import logging
from pathlib import Path
import sys
import importlib # Added for reloading
import configparser

# Add very early debug print
print("MAIN_DEBUG: engine.py script started.", file=sys.stderr, flush=True)

print("MAIN_DEBUG: Before importing models", file=sys.stderr, flush=True)
from models import Project, ProjectState, Turn # Import Project and ProjectState from models
print("MAIN_DEBUG: After importing models", file=sys.stderr, flush=True)

print("MAIN_DEBUG: Before importing persistence", file=sys.stderr, flush=True)
# Revert import to bring functions/classes directly into scope, and include necessary parts
from persistence import load_project_state, save_project_state, get_project_by_id, load_projects, save_projects, add_project, PersistenceError, DuplicateProjectError
# Removed: import persistence as persistence_module
print("MAIN_DEBUG: After importing persistence", file=sys.stderr, flush=True)

# Removed: import gemini_comms
print("MAIN_DEBUG: Before importing config_manager", file=sys.stderr, flush=True)
from config_manager import ConfigManager
print("MAIN_DEBUG: After importing config_manager", file=sys.stderr, flush=True)

# Try to import the mock factory, but don't fail if it's not there (e.g. deployment)
try:
    print("MAIN_DEBUG: Before importing gemini_comms_mocks", file=sys.stderr, flush=True)
    from gemini_comms_mocks import get_mock_communicator, MockGeminiCommunicatorBase
    print("DEBUG Engine: SUCCESSFULLY imported gemini_comms_mocks at top level.", file=sys.stderr)
    print("MAIN_DEBUG: After importing gemini_comms_mocks (Success)", file=sys.stderr, flush=True)
except ImportError as e_import_mock: # Catch the specific error
    get_mock_communicator = None
    MockGeminiCommunicatorBase = None # type: ignore # So type checker doesn't complain if it's None
    print("DEBUG Engine: Initial import of gemini_comms_mocks FAILED! VERY IMPORTANT DIAGNOSTIC!", file=sys.stderr)
    print(f"DEBUG Engine: Specific Error: {e_import_mock}", file=sys.stderr)
    print(f"DEBUG Engine: sys.path at failure: {sys.path}", file=sys.stderr)
    print(f"DEBUG Engine: CWD at failure: {os.getcwd()}", file=sys.stderr)
    print("MAIN_DEBUG: After importing gemini_comms_mocks (Failed)", file=sys.stderr, flush=True)
except Exception as e_general:
    print("MAIN_DEBUG: After importing gemini_comms_mocks (General Exception)", file=sys.stderr, flush=True)
    print(f"DEBUG Engine: General exception during gemini_comms_mocks import: {e_general}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    get_mock_communicator = None
    MockGeminiCommunicatorBase = None # type: ignore

# Get the logger instance (assuming it's configured in main.py or another central place)
# If not, this will create a default logger. For best practice, ensure it's configured.
print("MAIN_DEBUG: Before getting logger", file=sys.stderr, flush=True)
logger = logging.getLogger("orchestrator_prime")
print("MAIN_DEBUG: After getting logger", file=sys.stderr, flush=True)

class EngineState(Enum):
    IDLE = auto()
    LOADING_PROJECT = auto()
    PROJECT_SELECTED = auto()
    RUNNING_WAITING_INITIAL_GEMINI = auto()
    RUNNING_WAITING_LOG = auto()
    RUNNING_PROCESSING_LOG = auto()
    RUNNING_CALLING_GEMINI = auto()
    SUMMARIZING_CONTEXT = auto()
    PAUSED_WAITING_USER_INPUT = auto()
    TASK_COMPLETE = auto()
    ERROR = auto()
    # STOPPED state was removed as PROJECT_SELECTED or IDLE can represent a stopped task

class OrchestrationEngine:
    CURSOR_SOP_PROMPT_TEXT = """... (Full SOP content as defined previously) ...""" # Keep SOP text here
    GEMINI_CALL_TIMEOUT_SECONDS = 60  # Added class constant for Gemini API call timeout

    def __init__(self):
        print("MAIN_DEBUG: OrchestrationEngine.__init__ Start", file=sys.stderr, flush=True) # DEBUG
        logger.info("OrchestrationEngine initializing...")
        self.current_project: Optional[Project] = None
        self.current_project_state: Optional[ProjectState] = None
        self.state: EngineState = EngineState.IDLE
        self.gemini_comms_module = None 
        self.gemini_client = None 
        self.config_manager: Optional[ConfigManager] = None
        self.persistence_manager = None 
        self._active_mock_type: Optional[str] = None # Track if a mock is active
        try:
            print("MAIN_DEBUG: Engine.__init__: Before ConfigManager()", file=sys.stderr, flush=True) # DEBUG
            self.config_manager = ConfigManager()
            print("MAIN_DEBUG: Engine.__init__: After ConfigManager()", file=sys.stderr, flush=True) # DEBUG
            
            print("MAIN_DEBUG: Engine.__init__: Before _load_real_gemini_client()", file=sys.stderr, flush=True) # DEBUG
            self._load_real_gemini_client() # Initial load
            print("MAIN_DEBUG: Engine.__init__: After _load_real_gemini_client()", file=sys.stderr, flush=True) # DEBUG

            logger.info("OrchestrationEngine initialized.")
            print("MAIN_DEBUG: OrchestrationEngine.__init__ End (Success)", file=sys.stderr, flush=True) # DEBUG
        except PersistenceError as pe:
            logger.critical(f"Engine initialization failed due to PersistenceError: {pe}", exc_info=True)
            self._set_state(EngineState.ERROR, f"Persistence Error: {pe}")
            # No raise, allow engine to exist in error state
            print("MAIN_DEBUG: OrchestrationEngine.__init__ End (PersistenceError)", file=sys.stderr, flush=True) # DEBUG
        except Exception as e:
            logger.critical(f"Engine initialization failed: {e}", exc_info=True)
            self._set_state(EngineState.ERROR, f"Initialization failed: {e}")
            # No raise here either, to allow observation of the error state if possible
            print("MAIN_DEBUG: OrchestrationEngine.__init__ End (General Exception)", file=sys.stderr, flush=True) # DEBUG

        self.file_observer: Optional['Observer'] = None
        self._log_handler: Optional['LogFileCreatedHandler'] = None
        self.dev_logs_dir: str = ""
        self.dev_instructions_dir: str = ""
        self.last_error_message: Optional[str] = None if not hasattr(self, 'last_error_message') else self.last_error_message
        self.pending_user_question: Optional[str] = None
        self.status_message_for_display: Optional[str] = None
        self._last_critical_error: Optional[str] = None if not hasattr(self, '_last_critical_error') else self._last_critical_error
        self._cursor_timeout_timer: Optional[threading.Timer] = None
        self._shutdown_complete = False
        self._engine_lock = threading.RLock()
        self._gemini_call_thread: Optional[threading.Thread] = None
        self._gemini_response_queue = queue.Queue()
        self.pending_log_for_resumed_step: Optional[str] = None
        if self._last_critical_error:
             logger.error(f"Engine started with critical error: {self._last_critical_error}")

    def _set_state(self, new_state: EngineState, detail_message: Optional[str] = None):
        if self.state != new_state or detail_message:
            old_state_name = self.state.name
            self.state = new_state
            log_message_prefix = f"Engine state changed from {old_state_name} to {self.state.name}"

            if new_state == EngineState.ERROR:
                self.last_error_message = detail_message if detail_message else "Unknown error"
                logger.error(f"{log_message_prefix} - Error: {self.last_error_message}")
            elif new_state == EngineState.PAUSED_WAITING_USER_INPUT:
                self.pending_user_question = detail_message 
                logger.info(f"{log_message_prefix} - Waiting for user input. Question: {self.pending_user_question}")
            else:
                status_detail = f" - Detail: {detail_message}" if detail_message else ""
                logger.info(f"{log_message_prefix}{status_detail}")
            
            # Check if transitioning to a state where file watching should be active
            if new_state == EngineState.RUNNING_WAITING_LOG and self.current_project:
                 logger.debug(f"_set_state: Transitioned to RUNNING_WAITING_LOG. Ensuring file watcher is started.")
                 self._start_file_watcher()
            elif new_state != EngineState.RUNNING_WAITING_LOG:
                 logger.debug(f"_set_state: Transitioned to {new_state.name}. Stopping file watcher if active.")
                 self.stop_file_watcher()

            if self.current_project_state and self.current_project:
                self.current_project_state.current_status = self.state.name
                try:
                    save_project_state(self.current_project, self.current_project_state)
                    logger.debug(f"Saved project state for {self.current_project.name} with status {self.state.name}")
                except PersistenceError as e:
                    logger.error(f"Failed to save project state for {self.current_project.name}: {e}", exc_info=True)
                    self.last_error_message = f"Failed to save project state: {e}"

    def set_active_project(self, project_name: str) -> bool:
        with self._engine_lock:
            if self._last_critical_error:
                logger.error(f"set_active_project called but engine has critical error: {self._last_critical_error}")
                self._set_state(EngineState.ERROR, self._last_critical_error) # Use direct import name
                return False

            if project_name is None:
                logger.info("ENGINE_TRACE: set_active_project received None. Attempting to clear active project.")
                logger.info("ENGINE_TRACE: Calling stop_file_watcher.")
                self.stop_file_watcher()
                logger.info("ENGINE_TRACE: Returned from stop_file_watcher.")
                logger.info("ENGINE_TRACE: Calling _cancel_cursor_timeout.")
                self._cancel_cursor_timeout()
                logger.info("ENGINE_TRACE: Returned from _cancel_cursor_timeout.")
                self.current_project = None
                self.current_project_state = None
                logger.info("ENGINE_TRACE: Calling _set_state to IDLE.")
                self._set_state(EngineState.IDLE, "Active project cleared.") # Use direct import name
                logger.info("ENGINE_TRACE: Active project cleared. Engine is IDLE.")
                return True

            logger.info(f"Attempting to set active project to: {project_name}")
            self._set_state(EngineState.LOADING_PROJECT, f"Loading project: {project_name}...") # Use direct import name

            try:
                projects = load_projects()
                project_to_load: Optional[Project] = None
                for p in projects:
                    if p.name == project_name:
                        project_to_load = p
                        break

                if not project_to_load:
                    # Also print to stdout for terminal users and tests
                    print(f"Error: Project '{project_name}' not found.", file=sys.stderr, flush=True)
                    self._set_state(EngineState.IDLE, f"Project '{project_name}' not found.")
                    self.current_project = None
                    return False

                self.current_project = project_to_load
                logger.info(f"Setting active project to: {self.current_project.name}")

                # Load project state if it exists
                try:
                    loaded_state = load_project_state(self.current_project)
                    if loaded_state:
                        self.current_project_state = loaded_state
                        logger.info(f"Loaded existing state for project '{self.current_project.name}'.")
                        # Determine engine state based on loaded project state
                        if self.current_project_state.current_status == EngineState.PAUSED_WAITING_USER_INPUT.name: # Use direct import name
                             # If loaded state was waiting for input, transition to that state
                             self._set_state(EngineState.PAUSED_WAITING_USER_INPUT, self.current_project_state.pending_user_question) # Use direct import name
                        elif self.current_project_state.current_task_goal and self.current_project_state.current_status not in [EngineState.TASK_COMPLETE.name, EngineState.ERROR.name]: # Use direct import names
                             # If there's a goal and not in a terminal state, resume
                             # This is a simplification; actual resume logic might be more complex.
                             # For now, if it was RUNNING_WAITING_LOG or similar, go back there.
                             # If it was running, set it to RUNNING_WAITING_LOG to await next log.
                             # If it was IDLE or PROJECT_SELECTED with a goal, maybe it should start?
                             # Let's assume if there's a goal and status wasn't ERROR/COMPLETE, we go to WAITING_LOG
                             # unless the status explicitly says otherwise.
                             try:
                                  # Attempt to map saved status string back to EngineState enum
                                  saved_status_enum = EngineState[self.current_project_state.current_status]
                                  if saved_status_enum == EngineState.RUNNING_WAITING_LOG:
                                       self._set_state(EngineState.RUNNING_WAITING_LOG, "Resumed waiting for log.")
                                       # Need to check for a pending log file that might have been written while engine was off.
                                       # This logic should be in a dedicated resume method.
                                  else:
                                       self._set_state(saved_status_enum, f"Resumed in saved state: {saved_status_enum.name}")
                             except KeyError:
                                  logger.warning(f"Unknown saved engine state '{self.current_project_state.current_status}'. Setting to PROJECT_SELECTED.")
                                  self._set_state(EngineState.PROJECT_SELECTED, "Unknown saved state, reset to Project Selected.")

                        else:
                             # No active task/goal or in terminal state, stay in PROJECT_SELECTED
                             self._set_state(EngineState.PROJECT_SELECTED, "Project loaded.")
                    else:
                        # No saved state, start fresh for this project
                        self.current_project_state = ProjectState(project_id=self.current_project.id)
                        self._set_state(EngineState.PROJECT_SELECTED, "New project state created.")

                except PersistenceError as e:
                    logger.error(f"Failed to load state for project '{project_name}': {e}", exc_info=True)
                    self._set_state(EngineState.ERROR, f"Failed to load project state: {e}")
                    return False # Indicate failure
                except Exception as e:
                    logger.critical(f"Unexpected error loading project state for '{project_name}': {e}", exc_info=True)
                    self._set_state(EngineState.ERROR, f"Unexpected error loading project state: {e}")
                    return False # Indicate failure

            except PersistenceError as e:
                 # This outer catch handles errors during load_projects()
                 logger.error(f"Persistence Error during project loading for {project_name}: {e}", exc_info=True)
                 self._set_state(EngineState.ERROR, f"Persistence Error: {e}")
                 self.current_project = None
                 return False
            except Exception as e:
                 # This outer catch handles unexpected errors during load_projects()
                 logger.critical(f"Unexpected error during project loading for {project_name}: {e}", exc_info=True)
                 self._set_state(EngineState.ERROR, f"Unexpected error during project loading: {e}")
                 self.current_project = None
                 return False

            # Ensure project directories exist
            try:
                if not self.config_manager: # Should have been caught by init
                    raise ValueError("ConfigManager not initialized")
                self.dev_logs_dir = os.path.join(self.current_project.workspace_root_path, self.config_manager.get_default_dev_logs_dir())
                self.dev_instructions_dir = os.path.join(self.current_project.workspace_root_path, self.config_manager.get_default_dev_instructions_dir())
                os.makedirs(self.dev_logs_dir, exist_ok=True)
                os.makedirs(os.path.join(self.dev_logs_dir, "processed"), exist_ok=True)
                os.makedirs(self.dev_instructions_dir, exist_ok=True)
                logger.debug(f"Ensured project directories exist: Logs='{self.dev_logs_dir}', Instructions='{self.dev_instructions_dir}'")
            except (OSError, ValueError) as e:
                logger.error(f"File System Error: Failed to create project directories for {self.current_project.name}: {e}", exc_info=True)
                self._set_state(EngineState.ERROR, f"File System Error: Failed to create project directories: {e}")
                self.current_project = None
                return False

            final_state_to_set_name = self.current_project_state.current_status
            final_state_detail = None
            try:
                final_state_to_set = EngineState[final_state_to_set_name]
                logger.info(f"Project '{self.current_project.name}' loaded. Its state.json specified status: {final_state_to_set_name}")

                if final_state_to_set == EngineState.PAUSED_WAITING_USER_INPUT:
                    last_question = self._get_last_gemini_question_from_history()
                    final_state_detail = last_question if last_question else "Gemini is waiting for your input. Please check conversation history."
                    logger.info(f"Loaded into PAUSED_WAITING_USER_INPUT. Pending question: {final_state_detail}")
                    self._set_state(final_state_to_set, final_state_detail)
                # If loaded state is LOADING_PROJECT, or any other RUNNING_* state, or TASK_COMPLETE, or IDLE, transition to PROJECT_SELECTED
                # as the context for those states is lost upon restart. Only PAUSED_WAITING_USER_INPUT is fully restorable.
                elif final_state_to_set not in [EngineState.ERROR]: # Exclude ERROR, PAUSED handled above
                    logger.info(f"Project loaded with state {final_state_to_set.name}. Transitioning to PROJECT_SELECTED for a clean start.")
                    self._set_state(EngineState.PROJECT_SELECTED)
                    self.current_project_state.current_status = EngineState.PROJECT_SELECTED.name # Persist this clean state
                else: # Was: Preserve ERROR state if loaded. NOW: Handle loaded ERROR state by resetting.
                    loaded_error_state_name = final_state_to_set.name
                    logger.warning(f"Project '{self.current_project.name}' was loaded in an error state: {loaded_error_state_name}.")
                    # Transition to IDLE to allow selection or new task.
                    # PROJECT_SELECTED might be too presumptuous if the project data itself had an issue, though less likely here.
                    # IDLE is a safer neutral state post-error-load.
                    self._set_state(EngineState.IDLE)
                    self.current_project_state.current_status = EngineState.IDLE.name # Persist this reset

                    # Update main.py's display via engine attributes
                    self.status_message_for_display = f"NOTICE: Project '{self.current_project.name}' was loaded in state {loaded_error_state_name}. Reset to IDLE."
                    self.last_error_message = None # Clear the specific error message that led to the saved ERROR state.
                    # The general notice is in status_message_for_display

                    logger.info(f"Project '{self.current_project.name}' was in {loaded_error_state_name}, reset to IDLE. User notified.")

            except KeyError:
                 logger.error(f"Invalid state name '{final_state_to_set_name}' in project state for {self.current_project.name}. Resetting to PROJECT_SELECTED.", exc_info=True)
                 self._set_state(EngineState.PROJECT_SELECTED)
                 self.current_project_state.current_status = EngineState.PROJECT_SELECTED.name # Persist the correction

            save_project_state(self.current_project, self.current_project_state)
            logger.info(f"Active project successfully set. Project: '{self.current_project.name}', Engine state: '{self.state.name}'")

            # Load summarization interval from config
            self.current_project_state.summarization_interval = self.config_manager.get_summarization_interval()
            logger.info(f"Summarization interval for project '{self.current_project.name}' set to {self.current_project_state.summarization_interval} turns.")

            # Ensure project directories exist
            self.current_project_state.dev_instructions_path = Path(self.dev_instructions_dir).resolve()
            self.current_project_state.dev_logs_path = Path(self.dev_logs_dir).resolve()
            self.current_project_state.cursor_output_log_path = self.current_project_state.dev_logs_path / "cursor_step_output.txt"
            self.current_project_state.dev_instructions_path.mkdir(parents=True, exist_ok=True)
            self.current_project_state.dev_logs_path.mkdir(parents=True, exist_ok=True)

            # Add debug logging here after setting the final state during project load
            logger.debug(f"ENGINE_TRACE: Finished set_active_project for '{project_name}'. Final state: {self.state.name}")

            return True

    def _get_last_gemini_question_from_history(self) -> Optional[str]:
        if self.current_project_state and self.current_project_state.conversation_history:
            for turn in reversed(self.current_project_state.conversation_history):
                # Assuming Turn model has a 'needs_user_input' flag, or Gemini's role indicates it.
                # For now, let's assume questions are marked with a specific sender or a flag on the Turn object.
                # This needs to align with how `_add_to_history` stores questions.
                # A simple heuristic: last message from 'assistant' that implies a question.
                # This part is refactored in _add_to_history to use a `needs_user_input` flag on the Turn object.
                if turn.sender == "assistant" and turn.needs_user_input: # Check the flag
                    logger.debug(f"Found last Gemini question in history: '{turn.message[:50]}...'")
                    return turn.message
        logger.debug("No specific Gemini question found in history marked with needs_user_input.")
        return None

    def _start_file_watcher(self):
        logger.debug("_start_file_watcher: ENTERED.")
        # Move imports here
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler, FileCreatedEvent
            logger.debug("MAIN_DEBUG: Imported watchdog inside _start_file_watcher (Success)", file=sys.stderr, flush=True)
        except ImportError:
            Observer = None
            FileSystemEventHandler = None
            logger.error("watchdog library not found. Cannot monitor log files.")
            self._set_state(EngineState.ERROR, "File watcher (watchdog) not available. Cannot monitor log files.")
            return
        except Exception as e_general_watchdog:
            logger.error(f"DEBUG Engine: General exception during watchdog import inside _start_file_watcher: {e_general_watchdog}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            Observer = None
            FileSystemEventHandler = None
            logger.error(f"Error importing watchdog: {e_general_watchdog}. Cannot monitor log files.")
            self._set_state(EngineState.ERROR, f"Watcher Error: Failed to import watchdog: {e_general_watchdog}")
            return

        if not Observer or not FileSystemEventHandler:
             # This check is now redundant after the try-except block, but keeping for clarity
             # The return inside the except block handles the failure case.
             pass # Already handled by the import block above.

        # Move class definition here
        class LogFileCreatedHandler(FileSystemEventHandler): # type: ignore
            def __init__(self, engine: 'OrchestrationEngine'):
                super().__init__()
                self.engine = engine
                # Add debounce logic back in, accessing config_manager via self.engine
                self.last_event_time: Dict[str, float] = {}
                self.debounce_seconds = 2.0 # Default
                if not self.engine.config_manager:
                    logger.error("LogFileCreatedHandler initialized without engine.config_manager! Using default debounce.")
                else:
                    try:
                        self.debounce_seconds = self.engine.config_manager.get_watchdog_debounce_seconds()
                    except AttributeError as e: # Catch if method doesn't exist
                         logger.error(f"LogFileCreatedHandler: Error getting watchdog_debounce_seconds from config_manager: {e}. Using default.", exc_info=True)
                         self.debounce_seconds = 2.0 # Fallback
                    except Exception as e: # Catch any other unexpected error
                         logger.error(f"LogFileCreatedHandler: Unexpected error getting watchdog_debounce_seconds: {e}. Using default.", exc_info=True)
                         self.debounce_seconds = 2.0 # Fallback

            def on_created(self, event):
                logger.debug(f"on_created: Event type: {event.event_type}, Path: {event.src_path}")
                if event.is_directory:
                    logger.debug("on_created: Event is for a directory. Ignoring.")
                    return

                logger.debug(f"on_created: File created event for: {event.src_path}. Current engine state: {self.engine.state.name}")
                # Check if the created file is the specific agent output file we are waiting for
                # Access config_manager via self.engine
                expected_filename = self.engine.config_manager.get_cursor_output_filename()
                if os.path.basename(event.src_path).lower() == expected_filename.lower():
                     logger.info(f"on_created: Detected target log file: {os.path.basename(event.src_path)}. Triggering processing.")
                     # Queue the processing to the engine's main loop or a separate handler
                     # To avoid blocking the watchdog observer thread, we should not do heavy processing here.
                     # The current _on_log_file_created calls into _process_cursor_log directly, which might be blocking.
                     # Let's call _on_log_file_created from here, which already includes locking.
                     # Need to pass the full path.
                     try:
                         # Ensure path is absolute
                         abs_log_path = os.path.abspath(event.src_path)
                         logger.debug(f"on_created: Calling _on_log_file_created with absolute path: {abs_log_path}")
                         self.engine._on_log_file_created(abs_log_path)
                     except Exception as e: # Catch potential exceptions from _on_log_file_created
                          logger.error(f"on_created: Error calling _on_log_file_created: {e}", exc_info=True)
                else:
                     logger.debug(f"on_created: Created file is not the target log file ('{os.path.basename(event.src_path)}' != '{expected_filename}'). Ignoring.")

        if not self.current_project or not self.dev_logs_dir:
            logger.error("Cannot start file watcher: No active project or logs directory defined.")
            self._set_state(EngineState.ERROR, "Cannot start file watcher: No active project or logs directory defined.")
            return

        if self.file_observer and self.file_observer.is_alive():
            logger.debug("Attempting to stop existing file watcher before starting a new one...")
            self.stop_file_watcher()

        if self.file_observer: # Redundant check after stop_file_watcher potentially sets to None, but harmless
            logger.warning("file_observer was not None before creating a new one. Forcing nullification.")
            self.file_observer = None

        # Instantiate the handler *after* its definition and imports are within scope
        self._log_handler = LogFileCreatedHandler(self)
        self.file_observer = Observer()
        try:
            os.makedirs(self.dev_logs_dir, exist_ok=True)
            self.file_observer.schedule(self._log_handler, self.dev_logs_dir, recursive=False)
            self.file_observer.start()
            logger.info(f"File watcher started for directory: {self.dev_logs_dir}")
            logger.debug("_start_file_watcher: Observer thread started.")
        except Exception as e:
            logger.error(f"Error starting file watcher on {self.dev_logs_dir}: {e}", exc_info=True)
            self._set_state(EngineState.ERROR, f"Watcher Error: Failed to start file watcher: {e}")
            self.file_observer = None

    def stop_file_watcher(self):
        logger.debug("stop_file_watcher: ENTERED.")
        observer_to_stop = self.file_observer
        log_handler_to_clear = self._log_handler
        self.file_observer = None
        self._log_handler = None

        if observer_to_stop:
            if observer_to_stop.is_alive():
                logger.info("Stopping file watcher...")
                try:
                    observer_to_stop.stop()
                    observer_to_stop.join(timeout=5) 
                    if observer_to_stop.is_alive():
                        logger.warning("File watcher did not terminate in time after stop() and join().")
                    else:
                        logger.info("File watcher stopped successfully.")
                except Exception as e:
                    logger.error(f"Exception during file watcher stop/join: {e}", exc_info=True)
            else:
                logger.debug("File watcher was not alive when stop_file_watcher was called.")
        else:
            logger.debug("stop_file_watcher called but no observer instance was present.")
        logger.debug("stop_file_watcher: EXITED.")

    def _write_instruction_file(self, instruction: str):
        logger.debug(f"_write_instruction_file: Attempting to write instruction. Current project: {self.current_project.name if self.current_project else 'None'}, Instructions dir: {self.dev_instructions_dir}") # DEBUG
        if not self.current_project or not self.dev_instructions_dir:
            logger.error("_write_instruction_file: No active project or instructions directory.") # DEBUG
            self._set_state(EngineState.ERROR, "Cannot write instruction: No active project or instructions directory.")
            return
        if not self.current_project_state:
             logger.error("_write_instruction_file: No project state available.") # DEBUG
             self._set_state(EngineState.ERROR, "Cannot write instruction: No project state available.")
             return

        try:
            filename = self.config_manager.get_next_step_filename() # e.g., next_step.txt
            instruction_file_path = os.path.join(self.dev_instructions_dir, filename)
            logger.debug(f"_write_instruction_file: Target path: {instruction_file_path}") # DEBUG
            
            with open(instruction_file_path, 'w', encoding='utf-8') as f:
                f.write(instruction)
                f.flush() # Explicit flush
                # os.fsync(f.fileno()) # This might be too much, but an option for extreme cases
            # 'with open' handles close automatically
            logger.info(f"Instruction written to: {instruction_file_path}") # Moved log after write and close

            # Brief pause to ensure file system has time to process the write, especially for tests
            time.sleep(0.1) # Brief sleep after file is closed

            self.current_project_state.last_instruction_sent = instruction
            # History for Gemini's own instruction is added in _process_gemini_response before this call.
            # logger.info(f"Instruction written to: {instruction_file_path}") # Original position
            self._set_state(EngineState.RUNNING_WAITING_LOG, f"Instruction written. Waiting for Cursor log ('{self.config_manager.get_cursor_output_filename()}').")
            self._start_cursor_timeout()
            if not self.file_observer or not self.file_observer.is_alive(): # Start watcher if not already running
                logger.info("File watcher was not running. Starting it now for RUNNING_WAITING_LOG state.")
                self._start_file_watcher()

        except (OSError, PersistenceError) as e:
            logger.error(f"Failed to write instruction file '{filename}' to '{self.dev_instructions_dir}': {e}", exc_info=True)
            self._set_state(EngineState.ERROR, f"File Write Error: {e}")
        except AttributeError as ae: # e.g. if self.config_manager is None
             logger.critical(f"Configuration error while trying to write instruction file: {ae}", exc_info=True)
             self._set_state(EngineState.ERROR, f"Internal Configuration Error: {ae}")

    def _on_log_file_created(self, log_file_path: str):
        logger.debug(f"_on_log_file_created triggered for: {log_file_path}")
        with self._engine_lock:
            if self.state != EngineState.RUNNING_WAITING_LOG:
                logger.warning(f"Log file '{os.path.basename(log_file_path)}' created/detected, but engine not in RUNNING_WAITING_LOG state (current: {self.state.name}). Ignoring.")
                return

            self._cancel_cursor_timeout()
            self._set_state(EngineState.RUNNING_PROCESSING_LOG, f"Processing log file: {os.path.basename(log_file_path)}")
            
            try:
                # Add a small delay to ensure file is fully written and closed by Cursor agent
                time.sleep(self.config_manager.get_log_file_read_delay_seconds()) 
                with open(log_file_path, 'r', encoding='utf-8') as f:
                    log_content = f.read()
                logger.debug(f"Successfully read log file. Content length: {len(log_content)}")

                # Call _process_cursor_log which will call Gemini and then _process_gemini_response
                self._process_cursor_log(log_content) 

                # Move processed log file
                processed_dir = os.path.join(self.dev_logs_dir, "processed")
                os.makedirs(processed_dir, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                new_log_filename = f"{self.config_manager.get_cursor_output_filename().split('.')[0]}_{timestamp}.txt"
                try:
                    shutil.move(log_file_path, os.path.join(processed_dir, new_log_filename))
                    logger.info(f"Processed log moved to: {os.path.join(processed_dir, new_log_filename)}")
                except Exception as e_move:
                    logger.error(f"Failed to move processed log file '{log_file_path}' to '{processed_dir}': {e_move}", exc_info=True)
                    # Continue processing, moving is not critical for the main loop

            except FileNotFoundError:
                error_msg = f"Log file not found at path: {log_file_path} (event might be stale or file removed too quickly)"
                logger.error(error_msg)
                self._set_state(EngineState.ERROR, f"File Read Error: {error_msg}")
            except IOError as ioe:
                error_msg = f"IOError reading log file '{log_file_path}': {ioe}"
                logger.error(error_msg, exc_info=True)
                self._set_state(EngineState.ERROR, f"File Read Error: {error_msg}")
            except Exception as e:
                error_msg = f"Unexpected error processing log file '{log_file_path}': {e}"
                logger.critical(error_msg, exc_info=True)
                self._set_state(EngineState.ERROR, error_msg)

    def _process_cursor_log(self, log_content: str):
        print(f"DEBUG_PCL: _process_cursor_log ENTERED. Log content snippet: {log_content[:100]}", file=sys.stderr, flush=True)
        logger.info(f"PCL_INFO: _process_cursor_log ENTERED. Log content snippet: {log_content[:100]}")

        with self._engine_lock:
            print(f"DEBUG_PCL: _engine_lock ACQUIRED. Current state: {self.state.name}", file=sys.stderr, flush=True)
            logger.info(f"PCL_INFO: _engine_lock ACQUIRED. Current state: {self.state.name}")

            if not self.current_project or not self.current_project_state:
                logger.critical("PCL_CRIT: Cannot process cursor log: No active project or project state.")
                self._set_state(EngineState.ERROR, "Internal Error: Missing project context for log processing.")
                return

            self._add_to_history("CURSOR", log_content)
            self.current_project_state.gemini_turns_since_last_summary += 1

            if self.state == EngineState.RUNNING_PROCESSING_LOG:
                summarization_initiated = self._initiate_summarization_if_needed_and_set_state()

                if summarization_initiated:
                    logger.info("PCL_INFO: Summarization initiated by _process_cursor_log. Deferring next step instruction call.")
                    self.pending_log_for_resumed_step = log_content # Store log for when summary is done
                    # State is already SUMMARIZING_CONTEXT, main loop will handle summary response
                else:
                    logger.info("PCL_INFO: No summarization needed/initiated by _process_cursor_log. Proceeding with next step instruction call.")
                    self._set_state(EngineState.RUNNING_CALLING_GEMINI, "Calling Gemini after cursor log processing.")
                    self.pending_log_for_resumed_step = None # Clear any pending log

                    project_goal = self.current_project.overall_goal
                    history_copy = list(self.current_project_state.conversation_history)
                    current_summary = self.current_project_state.current_summary
                    max_hist_turns = self.config_manager.get_max_history_turns()
                    max_ctx_tokens = self.config_manager.get_max_context_tokens()
                    initial_project_structure_overview = None

                    self._gemini_call_thread = threading.Thread(
                        target=self._call_gemini_in_thread,
                        args=(
                            project_goal, history_copy, current_summary, 
                            max_hist_turns, max_ctx_tokens,
                            log_content, initial_project_structure_overview, 
                            self._gemini_response_queue,
                            False # is_summarization_call = False
                        ),
                        daemon=True, name=f"GeminiLogProcNextStepThread-{uuid.uuid4().hex[:8]}"
                    )
                    self._gemini_call_thread.start()
                    logger.info(f"PCL_INFO: Started Gemini call thread for NEXT STEP. Thread: {self._gemini_call_thread.name}")
            else:
                print(f"DEBUG_PCL: State is NOT RUNNING_WAITING_LOG (it is {self.state.name}). Not taking action in _process_cursor_log.", file=sys.stderr, flush=True)
                logger.warning(f"PCL_WARN: State is NOT RUNNING_WAITING_LOG (it is {self.state.name}). Not taking action in _process_cursor_log.")

    def _initiate_summarization_if_needed_and_set_state(self) -> bool:
        """Checks if summarization is needed. If yes, sets state to SUMMARIZING_CONTEXT, 
           queues the summarization call, and returns True. Otherwise, returns False.
        """
        with self._engine_lock:
            if not self.current_project or not self.current_project_state or not self.current_project.overall_goal:
                return False # Cannot summarize without project/goal

            should_summarize = (
                self.current_project_state.gemini_turns_since_last_summary >= self.config_manager.get_summarization_interval() and
                len(self.current_project_state.conversation_history) > 0
            )

            if should_summarize:
                logger.info("Summarization criteria met. Initiating context summarization.")
                self._set_state(EngineState.SUMMARIZING_CONTEXT, "Summarizing context before next Gemini call.")
                
                # Prepare arguments for summarization call
                project_goal = self.current_project.overall_goal
                history_copy = list(self.current_project_state.conversation_history)
                current_summary = self.current_project_state.current_summary
                max_tokens = self.config_manager.get_max_summary_tokens() # Assuming this config exists

                # Use a generic _call_gemini_in_thread but with a summarization-specific path inside it
                # The _call_gemini_in_thread will need to know it's a summarization call.
                # We can add a flag or a different target method for summarization if simpler.
                # For now, assume _call_gemini_in_thread can handle it by checking if log_content is None and goal is for summarization.
                # Or, more cleanly, add a is_summarization_call flag to _call_gemini_in_thread args.

                # Simplest: add 'is_summarization_call=True' to _call_gemini_in_thread
                # And modify _call_gemini_in_thread to use it.
                self._gemini_call_thread = threading.Thread(
                    target=self._call_gemini_in_thread, # This thread will call the actual summarization
                    args=(
                        project_goal, 
                        history_copy, 
                        current_summary, 
                        self.config_manager.get_max_history_turns(), # Not directly used by summarizer usually
                        max_tokens, # Max tokens for summary
                        None, # No specific cursor_log_content for summary call
                        None, # No initial_project_structure_overview for summary call
                        self._gemini_response_queue, # Main queue
                        True # is_summarization_call = True
                    ),
                    daemon=True,
                    name=f"GeminiSummaryThread-{uuid.uuid4().hex[:8]}"
                )
                self._gemini_call_thread.start()
                self.current_project_state.gemini_turns_since_last_summary = 0 # Reset counter
                return True
            return False

    def _call_gemini_in_thread(self, project_goal, full_history, current_summary, 
                               max_history_turns, max_context_tokens, 
                               cursor_log_content, initial_project_structure_overview, 
                               q_to_use: queue.Queue, is_summarization_call: bool = False):
        trace_id = uuid.uuid4().hex[:8]
        logger.info(f"GEMINI_THREAD ({trace_id}): STARTING. Summarization call: {is_summarization_call}. Goal: {project_goal[:30]}...")
        response = None
        try:
            if is_summarization_call:
                logger.info(f"GEMINI_THREAD ({trace_id}): Performing summarization call.")
                # Ensure text_to_summarize is correctly formed for summarize_conversation_history
                # This part of the logic might need review based on how summarize_conversation_history expects its input.
                # For now, assuming it handles the list of Turns correctly.
                summary_text = self.gemini_client.summarize_conversation_history(
                    history_turns=full_history, # full_history is List[Turn]
                    existing_summary=current_summary,
                    project_goal=project_goal,
                    max_tokens=self.config_manager.get_max_summary_tokens()
                )
                response = {"status": "SUCCESS_SUMMARY", "summary_text": summary_text, "id": trace_id}
            else:
                logger.info(f"GEMINI_THREAD ({trace_id}): Performing get_next_step call.")
                response = self.gemini_client.get_next_step_from_gemini(
                    project_goal=project_goal,
                    full_conversation_history=full_history,
                    current_context_summary=current_summary,
                    max_history_turns=max_history_turns,
                    max_context_tokens=max_context_tokens,
                    cursor_log_content=cursor_log_content,
                    initial_project_structure_overview=initial_project_structure_overview
                )
                # Add trace_id to the response for better tracking if it's a dict
                if isinstance(response, dict):
                    response['id'] = trace_id 
            
            logger.info(f"GEMINI_THREAD ({trace_id}): Call complete. Response: {str(response)[:200]}...")
            q_to_use.put(response)
            logger.info(f"GEMINI_THREAD ({trace_id}): Response put on queue.")

        except Exception as e_thread_gemini_call:
            logger.error(f"GEMINI_THREAD ({trace_id}): EXCEPTION during Gemini call or queue put: {e_thread_gemini_call}", exc_info=True)
            # Put an error indicator on the queue so the main thread doesn't hang indefinitely
            error_response = {
                "status": "THREAD_EXCEPTION", 
                "error": str(e_thread_gemini_call),
                "id": trace_id
            }
            try:
                q_to_use.put(error_response) 
                logger.info(f"GEMINI_THREAD ({trace_id}): THREAD_EXCEPTION response put on queue.")
            except Exception as e_queue_put_error:
                logger.error(f"GEMINI_THREAD ({trace_id}): CRITICAL - Failed to put THREAD_EXCEPTION on queue: {e_queue_put_error}", exc_info=True)
        finally:
            logger.info(f"GEMINI_THREAD ({trace_id}): FINISHED.")

    def _process_gemini_response(self, response_data: Dict[str, Any]):
        # (PGR_ENTRY and PRE_LOCK logs from before)
        print(f"DEBUG_PGR_ENTRY: _process_gemini_response. Action: {response_data.get('next_step_action')}", file=sys.stderr, flush=True)
        logger.critical(f"PGR_CRIT: _process_gemini_response ENTRY. Action: {response_data.get('next_step_action')}")
        print("DEBUG_PGR_PRE_LOCK: Attempting to acquire _engine_lock", file=sys.stderr, flush=True)
        logger.info("PGR_INFO: PRE_LOCK Attempting to acquire _engine_lock")

        with self._engine_lock:
            print("DEBUG_PGR_POST_LOCK: Acquired _engine_lock", file=sys.stderr, flush=True)
            logger.info("PGR_INFO: POST_LOCK Acquired _engine_lock")
            logger.info(f"PGR_INFO_STATE: Current engine state: {self.state.name}. Action: {response_data.get('next_step_action')}")
            logger.info(f"PGR_TRACE: Received response_data keys: {list(response_data.keys()) if response_data else 'None'}")
            # ... (log content of response_data) ...

            if self._shutdown_complete: # Check shutdown first
                 logger.warning("PGR_TRACE: Shutdown detected in _process_gemini_response. Ignoring.")
                 return

            action = response_data.get("next_step_action")
            trace_id = response_data.get("trace_id", "N/A")
            logger.info(f"PGR_INFO ({trace_id}): Processing action: {action}. Current state: {self.state.name}")

            if response_data.get("error") and action != "SUMMARY_COMPLETE": # Allow summary to proceed even if Gemini mock had an 'error' field but still returned a summary
                error_msg = response_data["error"]
                logger.error(f"PGR_ERROR ({trace_id}): Gemini response contained an error: {error_msg}")
                self._set_state(EngineState.ERROR, f"Gemini Error: {error_msg}")
                return

            if action == "SUMMARY_COMPLETE":
                if self.state != EngineState.SUMMARIZING_CONTEXT:
                    logger.warning(f"PGR_WARN ({trace_id}): Received SUMMARY_COMPLETE but state is {self.state.name}. Updating summary anyway.")
                
                summary_text = response_data.get("summary")
                if summary_text is not None:
                    self.current_project_state.current_summary = summary_text
                    self.current_project_state.gemini_turns_since_last_summary = 0 # Corrected attribute
                    logger.info(f"PGR_INFO ({trace_id}): Context summary updated. Length: {len(summary_text)}. Turns reset.")
                    save_project_state(self.current_project, self.current_project_state)
                else:
                    logger.error(f"PGR_ERROR ({trace_id}): SUMMARY_COMPLETE action but no summary text in response.")

                resumed_log_content = self.pending_log_for_resumed_step
                if resumed_log_content:
                    self.pending_log_for_resumed_step = None
                    logger.info(f"PGR_INFO ({trace_id}): Summary complete, resuming deferred next step call with stored log.")
                    self._set_state(EngineState.RUNNING_CALLING_GEMINI, "Resuming with stored log after summarization.")
                    
                    project_goal = self.current_project.overall_goal
                    history_copy = list(self.current_project_state.conversation_history)
                    # current_summary is now the NEW summary
                    max_hist_turns = self.config_manager.get_max_history_turns()
                    max_ctx_tokens = self.config_manager.get_max_context_tokens()
                    initial_project_structure_overview = None 

                    self._gemini_call_thread = threading.Thread(
                        target=self._call_gemini_in_thread,
                        args=(
                            project_goal, history_copy, self.current_project_state.current_summary, 
                            max_hist_turns, max_ctx_tokens,
                            resumed_log_content, initial_project_structure_overview, 
                            self._gemini_response_queue,
                            False # is_summarization_call = False
                        ),
                        daemon=True, name=f"GeminiResumedLogProcThread-{uuid.uuid4().hex[:8]}"
                    )
                    self._gemini_call_thread.start()
                else:
                    logger.info(f"PGR_INFO ({trace_id}): Summary complete. No pending log to resume. Setting state to RUNNING_WAITING_LOG.")
                    self._set_state(EngineState.RUNNING_WAITING_LOG, "Summary complete, awaiting next log/action.")
                    self._start_cursor_timeout() # Restart timeout as we are waiting for a log again

            elif action == "WRITE_TO_FILE":
                if self.state not in [EngineState.RUNNING_CALLING_GEMINI, EngineState.RUNNING_WAITING_INITIAL_GEMINI]:
                     logger.warning(f"PGR_WARN ({trace_id}): Received WRITE_TO_FILE but current state is {self.state.name}. Proceeding to write.")
                
                instruction = response_data.get("instruction")
                if instruction:
                    logger.info(f"PGR_INFO ({trace_id}): Instruction found in Gemini response: '{instruction[:100]}...' Action: {action}")
                    self._write_instruction_file(instruction)
                    self._add_to_history("GEMINI", instruction, needs_user_input=False)
                    self._set_state(EngineState.RUNNING_WAITING_LOG, "Wrote instruction, waiting for cursor log.")
                    self._start_cursor_timeout()
                else:
                    logger.error(f"PGR_ERROR ({trace_id}): Action was WRITE_TO_FILE but no instruction provided.")
                    self._set_state(EngineState.ERROR, "Gemini Error: Missing instruction for WRITE_TO_FILE.")
            
            # ... (handle other actions like REQUEST_USER_INPUT, TASK_COMPLETE, FATAL_ERROR) ...
            elif action == "REQUEST_USER_INPUT":
                # ... (similar logic, set state PAUSED_WAITING_USER_INPUT) ...
                pass # Placeholder
            elif action == "TASK_COMPLETE":
                # ... (set state TASK_COMPLETE) ...
                pass # Placeholder
            elif action == "FATAL_ERROR":
                # ... (set state ERROR) ... Already handled by error check at top if error field present
                logger.error(f"PGR_ERROR ({trace_id}): FATAL_ERROR action received.")
                self._set_state(EngineState.ERROR, response_data.get("error", "Fatal error from Gemini, no details."))

            else: # Unknown or unhandled action
                if not response_data.get("error"): # Avoid double logging if already handled as an error
                    logger.error(f"PGR_ERROR ({trace_id}): Unknown or unhandled Gemini action: '{action}'. Response: {str(response_data)[:200]}")
                    self._set_state(EngineState.ERROR, f"Unhandled Gemini Action: {action}")

    def start_task(self, initial_user_instruction: Optional[str] = None):
        """Starts a new task for the currently selected project."""
        # Add debug logging at the beginning of the method
        logger.debug(f"ENGINE_TRACE: start_task called with initial_user_instruction: '{initial_user_instruction[:50]}...'" if initial_user_instruction else "None")

        with self._engine_lock:
            if self._last_critical_error:
                self._set_state(EngineState.ERROR, self._last_critical_error)
                return
                
            if not self.current_project or not self.current_project_state:
                logger.error("Cannot start task: No active project selected or project state not loaded.")
                self._set_state(EngineState.ERROR, "No active project selected to start task.")
                return

            if self.state not in [EngineState.IDLE, EngineState.PROJECT_SELECTED, EngineState.TASK_COMPLETE, EngineState.ERROR]:
                if self.state == EngineState.PAUSED_WAITING_USER_INPUT and initial_user_instruction:
                    logger.info(f"Task being resumed via start_task with new instruction while in PAUSED_WAITING_USER_INPUT. User instruction: '{initial_user_instruction}'")
                    self.resume_with_user_input(initial_user_instruction) # Let resume handle it
                    return
                else:
                    logger.warning(f"Engine is busy (state: {self.state.name}). Please stop the current task or wait before starting a new one.")
                    # self.last_error_message = f"Engine busy ({self.state.name}). Stop task first." # Let main.py show status
                    return

            if self.state == EngineState.ERROR:
                logger.info("Attempting to start new task from an ERROR state. Clearing previous error.")
                self.last_error_message = None 
                self.pending_user_question = None 

            current_goal = initial_user_instruction if initial_user_instruction else self.current_project.overall_goal
            if not current_goal:
                logger.error("Cannot start task: No initial instruction and no overall project goal available.")
                self._set_state(EngineState.ERROR, "Cannot start task: No goal provided.")
                return

            self.current_project_state.current_task_goal = current_goal
            if initial_user_instruction:
                logger.info(f"Starting new task for project '{self.current_project.name}' with initial instruction. Clearing previous conversation history for this task segment.")
                self.current_project_state.conversation_history = [] 
                self.current_project_state.current_summary = "" 
                self.current_project_state.last_summary_turn_count = 0
            else:
                logger.info(f"Starting task for project '{self.current_project.name}' based on overall project goal. History NOT cleared.")

            self._add_to_history("user", current_goal, needs_user_input=False)
            self._set_state(EngineState.RUNNING_WAITING_INITIAL_GEMINI, f"Starting task: {current_goal[:100]}...")
            
            # For the very first call, we might include a project structure overview.
            initial_project_structure_overview = self._get_initial_project_structure_overview() if not self.current_project_state.conversation_history else None

            self._initiate_summarization_if_needed_and_set_state() # Corrected method name

            self._set_state(EngineState.RUNNING_CALLING_GEMINI, "Initial call to Gemini for new task.")
            self._gemini_call_thread = threading.Thread(
                target=self._call_gemini_in_thread,
                args=(
                    self.current_project.overall_goal,
                    self.current_project_state.conversation_history,
                    self.current_project_state.current_summary,
                    self.config_manager.get_max_history_turns(),
                    self.config_manager.get_max_context_tokens(),
                    None,
                    initial_project_structure_overview,
                    self._gemini_response_queue,
                ),
                daemon=True,
            )
            self._gemini_call_thread.start()

            try:
                # Timeout for Gemini call completion
                response_data = self._gemini_response_queue.get(timeout=self.GEMINI_CALL_TIMEOUT_SECONDS) 
                logger.info(f"Response received from Gemini queue: {response_data.get('status') if response_data else 'N/A'}") # Restored to simpler logging
                
                if response_data and response_data.get("error"):
                    error_msg = response_data["error"]
                    logger.error(f"Gemini call (initial) failed: {error_msg}")
                    self._set_state(EngineState.ERROR, f"Gemini Call Error: {error_msg}")
                elif response_data:
                    self._process_gemini_response(response_data) # Call _process_gemini_response directly
                else: 
                    logger.error("Response_data from queue was None. This is unexpected.") # Simplified message
                    self._set_state(EngineState.ERROR, "Internal Error: Empty response from Gemini task.")

            except queue.Empty:
                logger.error("Timeout waiting for Gemini response from thread.")
                self._set_state(EngineState.ERROR, "Timeout waiting for Gemini response.")
            except Exception as e:
                error_msg = f"An unexpected error occurred after initial Gemini call: {e}"
                logger.critical(error_msg, exc_info=True)
                self._set_state(EngineState.ERROR, error_msg)

            self.current_project_state.last_instruction_sent = None
            self.current_project_state.current_status = EngineState.RUNNING_WAITING_INITIAL_GEMINI.name

            try:
                # Save state immediately after updating for the new task
                save_project_state(self.current_project, self.current_project_state)
                logger.debug(f"Saved project state for {self.current_project.name} with status {self.state.name} after clearing history.")
            except PersistenceError as e:
                 logger.error(f"Failed to save project state for {self.current_project.name} after starting new task: {e}", exc_info=True)
                 # Decide if this is a critical failure or just a warning
                 # For now, log and continue, assuming the task might still proceed.
                 pass

            self._set_state(EngineState.RUNNING_WAITING_INITIAL_GEMINI, "Waiting for initial Gemini response.")
            logger.debug("ENGINE_TRACE: State set to RUNNING_WAITING_INITIAL_GEMINI.")
            print("Orchestrator Prime is thinking...", flush=True) # Added this print statement
            self.status_message_for_display = "Thinking..."
            logger.debug("ENGINE_TRACE: Printed 'Orchestrator Prime is thinking...'.")

            # Add debug logging at the end of the method
            logger.debug("ENGINE_TRACE: start_task finished.")

    def _get_initial_project_structure_overview(self) -> Optional[str]:
        if not self.current_project or not self.config_manager:
            return None
        try:
            max_files = self.config_manager.get_structure_max_files()
            max_dirs = self.config_manager.get_structure_max_dirs()
            excluded_patterns = self.config_manager.get_structure_excluded_patterns()
            
            workspace_path = self.current_project.workspace_root_path
            logger.debug(f"Generating initial structure overview for path: {workspace_path}")
            if not os.path.isdir(workspace_path):
                logger.warning(f"Workspace path '{workspace_path}' is not a valid directory for structure overview.")
                return f"[System Note: Workspace path '{workspace_path}' is not a directory.]"

            entries = os.listdir(workspace_path)
            files = []
            dirs = []
            
            def is_excluded(name, patterns):
                for pattern in patterns:
                    if (pattern.startswith("*") and name.endswith(pattern[1:])) or \
                       (pattern.endswith("*") and name.startswith(pattern[:-1])) or \
                       (pattern.startswith("*") and pattern.endswith("*") and pattern[1:-1] in name) or \
                       (name == pattern):
                        return True
                return False

            for entry_name in entries:
                if is_excluded(entry_name, excluded_patterns):
                    logger.debug(f"Excluding '{entry_name}' from structure overview due to exclude patterns.")
                    continue
                entry_path = os.path.join(workspace_path, entry_name)
                if os.path.isfile(entry_path) and len(files) < max_files:
                    files.append(entry_name)
                elif os.path.isdir(entry_path) and len(dirs) < max_dirs:
                    dirs.append(entry_name)
            
            structure_parts = []
            if files:
                structure_parts.append(f"Top-level files: {files}")
            if dirs:
                structure_parts.append(f"Top-level directories: {dirs}")
            
            if structure_parts:
                overview = f"Initial project structure overview (max {max_files} files, {max_dirs} dirs, excluding {excluded_patterns}): {repr(structure_parts)}."
                logger.debug(f"Generated structure overview: {overview}")
                return overview
            else:
                return f"[System Note: Project root '{workspace_path}' appears empty or all items excluded ({excluded_patterns}).]"

        except Exception as e:
            logger.error(f"Failed to generate project structure overview: {e}", exc_info=True)
            return f"[System Note: Error generating project structure overview: {e}]"

    def _load_real_gemini_client(self):
        """Loads the real Gemini client from gemini_comms_real.py."""
        logger.info("Attempting to load REAL Gemini client from gemini_comms_real...")
        module_name = "gemini_comms_real"
        try:
            # Ensure fresh import for reliability, especially if module was manipulated
            if module_name in sys.modules:
                logger.debug(f"Removing existing '{module_name}' from sys.modules for fresh import.")
                del sys.modules[module_name]
            
            # Attempt to import gemini_comms_real and its GeminiCommunicator class
            gemini_comms_real_module = importlib.import_module(module_name)
            RealGeminiCommunicator = getattr(gemini_comms_real_module, 'GeminiCommunicator')

            self.gemini_client = RealGeminiCommunicator()
            self._active_mock_type = None # Clear any mock type tracking
            logger.info(f"Successfully loaded REAL GeminiCommunicator from {module_name}. Client type: {type(self.gemini_client)}")

        except AttributeError:
            logger.error(f"'GeminiCommunicator' class not found in {module_name}.", exc_info=True)
            self._set_state(EngineState.ERROR, f"Real Gemini comms module class error.")
            self.gemini_client = None # Ensure client is None if loading fails
        except ImportError:
            logger.error(f"Failed to import REAL Gemini client module: {module_name}", exc_info=True)
            self._set_state(EngineState.ERROR, f"Real Gemini comms module import error.")
            self.gemini_client = None # Ensure client is None if loading fails
        except Exception as e:
            logger.error(f"Unexpected error loading REAL Gemini client from {module_name}: {e}", exc_info=True)
            self._set_state(EngineState.ERROR, f"Real Gemini comms module unknown error: {e}")
            self.gemini_client = None # Ensure client is None on any other unhandled exception

    def apply_mock_communicator(self, mock_type: str, details: Optional[Dict[str, Any]] = None) -> bool:
        """Applies a mock Gemini communicator."""
        with self._engine_lock:
            logger.info(f"Attempting to apply MOCK Gemini communicator of type: '{mock_type}' with details: {details}")
            
            mock_module_name = "gemini_comms_mocks"
            try:
                # Dynamically import here to get the latest version of the mock file
                if mock_module_name in sys.modules:
                    logger.debug(f"Removing existing '{mock_module_name}' from sys.modules for fresh mock import.")
                    del sys.modules[mock_module_name] # Ensure fresh import if mock file was just written/updated
                
                gemini_comms_mocks_module = importlib.import_module(mock_module_name)
                get_mock_communicator_func = getattr(gemini_comms_mocks_module, 'get_mock_communicator')
                MockGeminiCommunicatorBaseClass = getattr(gemini_comms_mocks_module, 'MockGeminiCommunicatorBase')

                mock_instance = get_mock_communicator_func(
                    mock_type,
                    details
                )

                if isinstance(mock_instance, MockGeminiCommunicatorBaseClass):
                    self.gemini_client = mock_instance
                    self._active_mock_type = mock_type
                    logger.info(f"Successfully applied MOCK Gemini communicator: '{mock_type}'. Current client: {type(self.gemini_client)}")
                    # Add print statement to output confirmation message for test script
                    print(f"Mock Gemini: {mock_type} applied for next call.", flush=True) 
                    return True
                else:
                    logger.error(f"Mock factory 'get_mock_communicator' returned unexpected type for '{mock_type}': {type(mock_instance)}. Expected a subclass of MockGeminiCommunicatorBase.")
                    self._active_mock_type = None 
                    self._load_real_gemini_client() # Revert to real client on error
                    return False
            except AttributeError: # Handles missing functions/classes in the mock module
                logger.error(f"Attribute error while loading or using mock communicator from '{mock_module_name}'. Functions/classes might be missing.", exc_info=True)
                self._active_mock_type = None
                self._load_real_gemini_client()
                self._set_state(EngineState.ERROR, f"Mocking system attribute error for '{mock_type}'.")
                return False
            except ImportError: # Handles if gemini_comms_mocks.py itself is not found
                logger.error(f"Failed to import MOCK Gemini client module: '{mock_module_name}'. File might be missing.", exc_info=True)
                self._active_mock_type = None
                # Don't necessarily revert to real client here, as the intent was to mock. Error state is better.
                self._set_state(EngineState.ERROR, f"Mocking system import error for '{mock_type}'.")
                return False
            except Exception as e:
                logger.error(f"Error applying mock communicator '{mock_type}': {e}", exc_info=True)
                self._active_mock_type = None 
                self._load_real_gemini_client() # Revert to real client on other errors
                self._set_state(EngineState.ERROR, f"Error applying mock '{mock_type}': {e}")
                return False

    def reinitialize_gemini_client(self) -> bool:
        """Reinitializes to the REAL Gemini client, removing any mocks."""
        with self._engine_lock:
            logger.info("Re-initializing to REAL Gemini client (removing any active mocks)...")
            self._load_real_gemini_client()
            if self.gemini_client:
                logger.info("Successfully reverted to REAL Gemini client.")
                return True
            else:
                logger.error("Failed to revert to REAL Gemini client. Engine may be in error state.")
                return False

    def get_project_path(self):
        # This method seems unused and current_project might not have a direct 'path' attribute.
        # It likely intended to return current_project.workspace_root_path
        if self.current_project:
            return self.current_project.workspace_root_path
        return None

    def get_current_engine_state_name(self): # Renamed for clarity
        return self.state.name

    def _get_timestamp(self) -> str:
        return datetime.now().isoformat()

    def _add_to_history(self, sender: str, message: str, needs_user_input: bool = False):
        """Adds a turn to the conversation history and saves project state."""
        if not self.current_project or not self.current_project_state:
            logger.warning("Attempted to add to history with no active project or state.")
            return

        turn = Turn(sender=sender, message=message, timestamp=self._get_timestamp())
        
        self.current_project_state.conversation_history.append(turn)
        
        # Logic for pending_user_question being set or cleared:
        # - Set by _process_gemini_response if action is REQUEST_USER_INPUT.
        # - Cleared here if sender is USER, or by _process_gemini_response for other actions.
        if sender == "USER":
            self.current_project_state.pending_user_question = None 

        try:
            save_project_state(self.current_project, self.current_project_state)
            logger.debug(f"Added to history for {self.current_project.name}: [{sender}] - '{message[:50]}...'. History len: {len(self.current_project_state.conversation_history)}")
        except PersistenceError as e:
            logger.error(f"Failed to save project state after adding to history for {self.current_project.name}: {e}", exc_info=True)

    def _start_cursor_timeout(self):
        with self._engine_lock:
            self._cancel_cursor_timeout() # Always cancel previous before starting new
            if self.state == EngineState.RUNNING_WAITING_LOG: 
                timeout_seconds = self.config_manager.get_cursor_log_timeout_seconds()
                logger.info(f"Starting cursor log timeout for {timeout_seconds}s.")
                self._cursor_timeout_timer = threading.Timer(timeout_seconds, self._handle_cursor_timeout)
                self._cursor_timeout_timer.daemon = True # Ensure timer doesn't block program exit
                self._cursor_timeout_timer.start()
            else:
                logger.debug(f"Cursor timeout not started. Engine state is {self.state.name}, not RUNNING_WAITING_LOG.")

    def _cancel_cursor_timeout(self):
        with self._engine_lock:
            if self._cursor_timeout_timer:
                logger.info("Cancelling existing cursor timeout timer.")
                self._cursor_timeout_timer.cancel()
                try:
                    # Only join if the timer was started and might be running
                    # A timer is 'alive' after start() until its function finishes or it's cancelled and joined.
                    if self._cursor_timeout_timer.is_alive(): 
                        logger.debug("Waiting for cursor timeout timer thread to finish...")
                        self._cursor_timeout_timer.join(timeout=1.0) # Short join timeout
                        if self._cursor_timeout_timer.is_alive():
                            logger.warning("Cursor timeout timer thread did not finish after join(1.0).")
                        else:
                            logger.debug("Cursor timeout timer thread finished after join.")
                except RuntimeError as e:
                    logger.warning(f"RuntimeError during timer join: {e}. This might occur if timer was cancelled before start.")
                except Exception as e_join: # Catch any other unexpected join error
                    logger.error(f"Unexpected error joining cursor timer: {e_join}", exc_info=True)
                self._cursor_timeout_timer = None
                logger.debug("Cursor timeout timer object set to None.")
            else:
                logger.debug("Request to cancel cursor timeout, but no timer was active.")

    def _handle_cursor_timeout(self):
        with self._engine_lock:
            # Check if the timeout is still relevant (e.g., state hasn't changed, task not stopped)
            if self.state == EngineState.RUNNING_WAITING_LOG and self.current_project_state and not self._shutdown_complete:
                error_msg = (
                    f"Cursor log timeout: No log file ('{self.config_manager.get_cursor_output_filename()}') "
                    f"received from Cursor agent within {self.config_manager.get_cursor_log_timeout_seconds()} seconds "
                    f"for project '{self.current_project.name if self.current_project else 'N/A'}'."
                )
                logger.error(error_msg)
                self._add_to_history("SYSTEM_ERROR", error_msg) # Use a distinct sender
                
                self.stop_file_watcher() # Stop watcher, it failed to see the file for this round.

                logger.info("Cursor log timed out. Asking Gemini for next step...")
                self._set_state(EngineState.RUNNING_CALLING_GEMINI, "Cursor log timed out. Consulting Gemini.")

                # Prepare args for Gemini call
                project_goal = self.current_project.overall_goal
                history_copy = list(self.current_project_state.conversation_history)
                current_summary = self.current_project_state.current_summary
                max_hist_turns = self.config_manager.get_max_history_turns()
                max_ctx_tokens = self.config_manager.get_max_context_tokens()
                
                # Special log content for Gemini indicating timeout
                timeout_log_for_gemini = (
                    f"SYSTEM_NOTE: Cursor agent did not produce a log file named "
                    f"'{self.config_manager.get_cursor_output_filename()}' in the expected time. "
                    f"The last instruction sent to the agent was: '{self.current_project_state.last_instruction_sent or 'Not available'}'."
                    f"What should be the next course of action?"
                )

                self._gemini_call_thread = threading.Thread(
                    target=self._call_gemini_in_thread,
                    args=(
                        project_goal, history_copy, current_summary, 
                        max_hist_turns, max_ctx_tokens,
                        timeout_log_for_gemini, # Use the special timeout log
                        None, # No initial project structure overview for this call
                        self._gemini_response_queue,
                        False # is_summarization_call = False
                    ),
                    daemon=True, name=f"GeminiCursorTimeoutHandlerThread-{uuid.uuid4().hex[:8]}"
                )
                self._gemini_call_thread.start()
            elif self._shutdown_complete:
                logger.info(f"Cursor timeout handler triggered during shutdown. No action taken.")
            else:
                logger.info(f"Cursor timeout handler triggered, but state is {self.state.name} (not RUNNING_WAITING_LOG) or no project. No action taken.")
            self._cursor_timeout_timer = None # Clear the timer since it has fired

    def print_help(self):
        """Prints the help message to standard output."""
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

    def print_status(self):
        """Prints the current engine status and active project."""
        print("--- Current Status ---")
        print(f"Engine State: {self.state.name}")
        if self.current_project:
            print(f"Active Project: {self.current_project.name}")
            print(f"Workspace: {self.current_project.workspace_root_path}")
            if self.current_project_state:
                print(f"Project State File: {self.current_project.workspace_root_path}/.orchestrator_state/state.json")
                if self.current_project_state.current_task_goal:
                    print(f"Current Task Goal: {self.current_project_state.current_task_goal[:100]}...")
                if self.current_project_state.current_summary:
                    print(f"Current Context Summary Length: {len(self.current_project_state.current_summary)}")
                if self.current_project_state.pending_user_question:
                     print(f"Waiting for User Input: {self.current_project_state.pending_user_question[:100]}...")
        else:
            print("Active Project: None")
        if self.last_error_message:
            print(f"Last Error: {self.last_error_message}")
        print("--------------------")

    def process_command(self, command_string: str) -> bool: # Return True if command processed, False otherwise
        """Processes a single command string from the user."""
        command_string = command_string.strip()
        if not command_string:
            return False # No command to process

        parts = command_string.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        logger.debug(f"Processing command: {command}, args: {args}")

        if self.state == EngineState.PAUSED_WAITING_USER_INPUT:
            if command == "input":
                if args:
                    print(f"--- Resuming with your input: '{args}' ---")
                    self.resume_with_user_input(args)
                    return True
                else:
                    print("--- Input cannot be empty. Please provide a response. ---")
                    return False
            elif command == "help":
                 self.print_help()
                 return True
            else:
                print(f"--- Engine is waiting for input. Use 'input <your response>' or see 'help'. ---")
                if self.pending_user_question:
                     print(f"Gemini's Question: {self.pending_user_question}")
                return False

        # Handle commands not dependent on state PAUSED_WAITING_USER_INPUT
        if command == "quit":
            print("--- Shutting down Orchestrator Prime... ---")
            self.shutdown()
            return True

        elif command == "help":
            self.print_help()
            return True

        elif command == "status":
            self.print_status()
            return True

        # Commands requiring a project to be selected
        if not self.current_project:
             if command in ["goal", "stop", "project", "input"] and command != "project": # input is handled above, project commands handled below
                 print("--- No project selected. Use 'project select <name>'. ---")
                 return False

        # Project commands
        if command == "project":
             project_parts = args.split(maxsplit=1)
             project_command = project_parts[0].lower() if project_parts else ""
             project_args = project_parts[1] if len(project_parts) > 1 else ""

             logger.debug(f"PROJECT COMMAND HANDLING: project_command={project_command}, project_args={project_args}")

             if project_command == "list":
                 logger.debug("PROJECT COMMAND HANDLING: Entering list block")
                 # Need to load projects here
                 try:
                     projects = load_projects()
                     if projects:
                         print("--- Available Projects: ---")
                         for proj in projects:
                             print(f"  - {proj.name}")
                     else:
                         print("--- No projects found. Use 'project add' to create one. ---")
                     return True
                 except PersistenceError as e:
                     print(f"Error listing projects: {e}")
                     logger.error(f"Error listing projects: {e}", exc_info=True)
                     return False
             
             elif project_command == "add" or project_command == "create":
                  logger.debug("PROJECT COMMAND HANDLING: Entering add/create block")
                  print("--- Adding a new project ---")
                  # This command requires interactive input, which is hard to simulate here.
                  # For test purposes, projects should likely be pre-created or added via test setup.
                  # print("NOTE: 'project add' command requires interactive input not supported directly here.") # Remove this note as we support non-interactive via args
                  # print("Please manage projects via the test setup or manually.") # Remove this note
                  # Attempt to parse name and path if provided non-interactively (not standard usage)
                  add_args = project_args.split(maxsplit=1)
                  name = add_args[0] if add_args else ""
                  root_path_str = add_args[1] if len(add_args) > 1 else ""

                  if name and root_path_str:
                       root_path = Path(root_path_str).resolve()
                       if not root_path.is_dir():
                            print(f"Error: Workspace root path '{root_path}' is not a valid directory.")
                            return False
                       try:
                            new_project = Project(id=str(uuid.uuid4()), name=name, workspace_root_path=str(root_path), overall_goal="Set goal using 'goal' command") # Default goal
                            add_project(new_project)
                            print(f"Project '{name}' created at '{root_path}'.")
                            return True
                       except DuplicateProjectError: # Explicitly qualify DuplicateProjectError
                            print(f"Error: Project '{name}' already exists.")
                            return False
                       except PersistenceError as e:
                            print(f"Error adding project: {e}")
                            logger.error(f"Error adding project: {e}", exc_info=True)
                            return False
                  else:
                       print("Usage: project add/create <name> <workspace_root_path>") # Update usage
                       return False

             elif project_command == "select":
                  logger.debug("PROJECT COMMAND HANDLING: Entering select block")
                  if args:
                      project_name_to_select = project_args.strip()
                      if self.set_active_project(project_name_to_select):
                           print(f"Project '{project_name_to_select}' selected.")
                           # Update the prompt in run_terminal_interface? No, that's main's responsibility.
                           # The engine just manages internal state.
                           return True
                      else:
                           # Error message already printed by set_active_project
                           return False
                  else:
                      print("Usage: project select <name>")
                      return False
             
             elif project_command == "delete":
                 logger.debug("PROJECT COMMAND HANDLING: Entering delete block")
                 project_name_to_delete = project_args.strip()
                 print(f"Attempting to delete project: {project_command} {project_name_to_delete}")
                 try:
                     # Load all projects
                     projects = load_projects()
                     project_to_delete = None
                     for proj in projects:
                         if proj.name == project_name_to_delete:
                             project_to_delete = proj
                             break
                     if not project_to_delete:
                         print(f"Error: Project '{project_name_to_delete}' not found.")
                         return False
                     # Remove from projects list and save
                     projects = [proj for proj in projects if proj.name != project_name_to_delete]
                     save_projects(projects)
                     # Delete project directory
                     import shutil
                     proj_dir = Path(project_to_delete.workspace_root_path)
                     if proj_dir.exists():
                         shutil.rmtree(proj_dir, ignore_errors=True)
                     print(f"Project '{project_name_to_delete}' deleted.")
                     return True
                 except Exception as e:
                     print(f"Error deleting project '{project_name_to_delete}': {e}")
                     logger.error(f"Error deleting project '{project_name_to_delete}': {e}", exc_info=True)
                     return False

             else:
                 logger.debug(f"PROJECT COMMAND HANDLING: Entering unknown command block for: {project_command}")
                 print(f"Error: Unknown project command: {project_command}")
                 print("Usage: project [list|add|select|delete]")
                 return False

        elif command == "goal":
            if not self.current_project:
                print("--- No project selected. Use 'project select <name>' before setting a goal. ---")
                return False
            if not args:
                print("--- Goal cannot be empty. Please provide an initial goal. ---")
                return False
            
            # Call engine method to start task with goal
            self.start_task(initial_user_instruction=args)
            return True

        elif command == "stop":
             # Need stop logic here or call an engine method
             print("NOTE: 'stop' command not yet fully implemented.")
             # Placeholder
             # if self.state in [RUNNING states]:
             #    self.stop_task()
             #    print("--- Task stopped. ---")
             # else:
             #    print("--- No active task to stop. ---")
             return False # Indicate not fully functional yet

        # If not a recognized command, treat as initial goal if project selected and not busy
        # This logic should be in main's loop after calling process_command if it returns False
        # For now, just report unknown command if it falls through here.

        else:
            print(f"Error: Invalid command '{command}'. Type 'help' for a list of commands.")
            return False

# Removed dummy_gui_callback and if __name__ == '__main__' block for OrchestrationEngine
# This module is intended to be imported, not run directly as the main script.
