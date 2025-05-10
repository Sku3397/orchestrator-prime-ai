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

from models import Project, ProjectState, Turn
from persistence import load_project_state, save_project_state, get_project_by_id, load_projects, PersistenceError
# Removed: import gemini_comms
from config_manager import ConfigManager

# Get the logger instance (assuming it's configured in main.py or another central place)
# If not, this will create a default logger. For best practice, ensure it's configured.
logger = logging.getLogger("orchestrator_prime")

# Watchdog is an external dependency, ensure it's handled if not available
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent
except ImportError:
    Observer = None
    FileSystemEventHandler = None
    logger.warning("watchdog library not found. File watching will be disabled.")

class EngineState(Enum):
    IDLE = auto()
    LOADING_PROJECT = auto()
    PROJECT_SELECTED = auto()
    RUNNING_WAITING_INITIAL_GEMINI = auto()
    RUNNING_WAITING_LOG = auto()
    RUNNING_PROCESSING_LOG = auto()
    RUNNING_CALLING_GEMINI = auto()
    PAUSED_WAITING_USER_INPUT = auto()
    TASK_COMPLETE = auto()
    ERROR = auto()
    # STOPPED state was removed as PROJECT_SELECTED or IDLE can represent a stopped task

class OrchestrationEngine:
    CURSOR_SOP_PROMPT_TEXT = """... (Full SOP content as defined previously) ...""" # Keep SOP text here
    GEMINI_CALL_TIMEOUT_SECONDS = 60  # Added class constant for Gemini API call timeout

    def __init__(self):
        print("DEBUG Engine.__init__: Start", file=sys.stderr) # DEBUG
        logger.info("OrchestrationEngine initializing...")
        self.current_project: Optional[Project] = None
        self.current_project_state: Optional[ProjectState] = None
        self.state: EngineState = EngineState.IDLE
        self.gemini_comms_module = None 
        self.gemini_client = None 
        self.config_manager: Optional[ConfigManager] = None
        self.persistence_manager = None 
        try:
            print("DEBUG Engine.__init__: Before ConfigManager()", file=sys.stderr) # DEBUG
            self.config_manager = ConfigManager()
            print("DEBUG Engine.__init__: After ConfigManager()", file=sys.stderr) # DEBUG
            
            self._load_gemini_comms_and_client() # Initial load

            logger.info("OrchestrationEngine initialized.")
            print("DEBUG Engine.__init__: End", file=sys.stderr) # DEBUG
        except PersistenceError as pe:
            logger.critical(f"Engine initialization failed due to PersistenceError: {pe}", exc_info=True)
            self._set_state(EngineState.ERROR, f"Persistence Error: {pe}")
            # No raise, allow engine to exist in error state
        except Exception as e:
            logger.critical(f"Engine initialization failed: {e}", exc_info=True)
            self._set_state(EngineState.ERROR, f"Initialization failed: {e}")
            # No raise here either, to allow observation of the error state if possible

        self.file_observer: Optional['Observer'] = None
        self._log_handler: Optional['LogFileCreatedHandler'] = None
        self.dev_logs_dir: str = ""
        self.dev_instructions_dir: str = ""
        self.last_error_message: Optional[str] = None if not hasattr(self, 'last_error_message') else self.last_error_message
        self.pending_user_question: Optional[str] = None
        self._last_critical_error: Optional[str] = None if not hasattr(self, '_last_critical_error') else self._last_critical_error
        self._cursor_timeout_timer: Optional[threading.Timer] = None
        self._shutdown_complete = False
        self._engine_lock = threading.Lock()
        self._gemini_call_thread: Optional[threading.Thread] = None
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
                self._set_state(EngineState.ERROR, self._last_critical_error)
                return False

            logger.info(f"Attempting to set active project to: {project_name}")
            self._set_state(EngineState.LOADING_PROJECT, f"Loading project: {project_name}...")
            
            projects = load_projects() # This can raise PersistenceError
            project_to_load: Optional[Project] = None
            for p in projects:
                if p.name == project_name:
                    project_to_load = p
                    break
            
            if not project_to_load:
                self._set_state(EngineState.ERROR, f"Project '{project_name}' not found.")
                self.current_project = None
                return False

            self.current_project = project_to_load
            logger.info(f"Setting active project to: {self.current_project.name}")

            try:
                loaded_state = load_project_state(self.current_project)
            except PersistenceError as e:
                logger.error(f"Error loading project state for {self.current_project.name}: {e}", exc_info=True)
                self._set_state(EngineState.ERROR, f"Persistence Error: Failed to load state for {self.current_project.name}: {e}")
                self.current_project = None
                return False

            if loaded_state:
                self.current_project_state = loaded_state
                logger.debug(f"Loaded project state for {self.current_project.name}. Raw status from file: '{self.current_project_state.current_status}'.")
                # Reset non-terminal error states or intermediate states from previous sessions
                current_status_from_file = self.current_project_state.current_status
                try:
                    loaded_enum_state = EngineState[current_status_from_file]
                    if loaded_enum_state.name.startswith("ERROR") or \
                       loaded_enum_state in [EngineState.RUNNING_WAITING_INITIAL_GEMINI, 
                                            EngineState.RUNNING_WAITING_LOG, 
                                            EngineState.RUNNING_PROCESSING_LOG, 
                                            EngineState.RUNNING_CALLING_GEMINI]:
                        logger.warning(f"Project {self.current_project.name} loaded with a previous transient or error status ('{current_status_from_file}'). Resetting to PROJECT_SELECTED.")
                        self.current_project_state.current_status = EngineState.PROJECT_SELECTED.name
                    elif loaded_enum_state == EngineState.PAUSED_WAITING_USER_INPUT:
                        # This state is okay to load into, will be handled further down.
                        pass 
                except KeyError: # Not a valid EngineState name
                    logger.warning(f"Project {self.current_project.name} loaded with an invalid status string ('{current_status_from_file}'). Resetting to PROJECT_SELECTED.")
                    self.current_project_state.current_status = EngineState.PROJECT_SELECTED.name

            else:
                logger.info(f"No existing project state for {self.current_project.name}. Creating new state.")
                proj_id = self.current_project.id if self.current_project.id else str(uuid.uuid4())
                if not self.current_project.id:
                    self.current_project.id = proj_id
                    # Note: This modification to project.id should be saved back to the main projects list.
                    # This is currently a gap if a new project (not yet in projects.json) is selected.
                    # add_project in persistence.py should assign an ID if not present and save it.
                    logger.warning(f"Project '{self.current_project.name}' was missing an ID. Assigned: {proj_id}. This needs saving to projects.json.")
                self.current_project_state = ProjectState(project_id=proj_id)
            
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
                else: # Preserve ERROR state if loaded
                    self._set_state(final_state_to_set, "Project loaded into an existing ERROR state.")

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
        if not Observer or not FileSystemEventHandler:
            logger.error("File watcher (watchdog) not available. Cannot monitor log files.")
            self._set_state(EngineState.ERROR, "File watcher (watchdog) not available. Cannot monitor log files.")
            return
        if not self.current_project or not self.dev_logs_dir:
            logger.error("Cannot start file watcher: No active project or logs directory defined.")
            self._set_state(EngineState.ERROR, "Cannot start file watcher: No active project or logs directory defined.")
            return

        if self.file_observer and self.file_observer.is_alive():
            logger.debug("Attempting to stop existing file watcher before starting a new one...")
            self.stop_file_watcher()

        if self.file_observer:
            logger.warning("file_observer was not None before creating a new one. Forcing nullification.")
            self.file_observer = None

        self._log_handler = LogFileCreatedHandler(self)
        self.file_observer = Observer()
        try:
            os.makedirs(self.dev_logs_dir, exist_ok=True)
            self.file_observer.schedule(self._log_handler, self.dev_logs_dir, recursive=False)
            self.file_observer.start()
            logger.info(f"File watcher started for directory: {self.dev_logs_dir}")
        except Exception as e:
            logger.error(f"Error starting file watcher on {self.dev_logs_dir}: {e}", exc_info=True)
            self._set_state(EngineState.ERROR, f"Watcher Error: Failed to start file watcher: {e}")
            self.file_observer = None

    def stop_file_watcher(self):
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

    def _write_instruction_file(self, instruction: str):
        if not self.current_project or not self.dev_instructions_dir:
            self._set_state(EngineState.ERROR, "Cannot write instruction: No active project or instructions directory.")
            return
        if not self.current_project_state:
             self._set_state(EngineState.ERROR, "Cannot write instruction: No project state available.")
             return

        try:
            filename = self.config_manager.get_next_step_filename() # e.g., next_step.txt
            instruction_file_path = os.path.join(self.dev_instructions_dir, filename)
            self._write_to_file(self.dev_instructions_dir, filename, instruction)
            
            self.current_project_state.last_instruction_sent = instruction
            # History for Gemini's own instruction is added in _process_gemini_response before this call.
            logger.info(f"Instruction written to: {instruction_file_path}")
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
        if not self.current_project or not self.current_project_state:
            logger.critical("Cannot process cursor log: No active project or project state.")
            self._set_state(EngineState.ERROR, "Internal Error: Missing project context for log processing.")
            return

        self._add_to_history("cursor_log", log_content, needs_user_input=False)
        self._set_state(EngineState.RUNNING_CALLING_GEMINI, "Calling Gemini with new log content...")
        
        gemini_q: queue.Queue[Dict[str, Any]] = queue.Queue()
        self._gemini_call_thread = threading.Thread(
            target=self._call_gemini_in_thread,
            args=(
                self.current_project.overall_goal,
                self.current_project_state.conversation_history,
                self.current_project_state.current_summary,
                None, # No initial structure overview for subsequent calls
                log_content, # Pass the new log content
                gemini_q
            ),
            daemon=True,
            name="GeminiLogProcessingThread"
        )
        self._gemini_call_thread.start()

        try:
            # Timeout for Gemini call completion
            response_data = gemini_q.get(timeout=self.GEMINI_CALL_TIMEOUT_SECONDS) # Use self.
            logger.info(f"Response received from Gemini queue: {response_data.get('status')}")
            if response_data.get("error"):
                error_msg = response_data["error"]
                logger.error(f"Gemini call (after log) failed: {error_msg}")
                self._set_state(EngineState.ERROR, f"Gemini Call Error: {error_msg}")
            else:
                self._process_gemini_response(response_data)
        except queue.Empty:
            logger.error("Timeout waiting for Gemini response from thread.")
            self._set_state(EngineState.ERROR, "Timeout waiting for Gemini response.")
        except Exception as e:
            error_msg = f"Unexpected error after Gemini call (log processing): {e}"
            logger.critical(error_msg, exc_info=True)
            self._set_state(EngineState.ERROR, error_msg)

    def _add_to_history(self, sender: str, message: str, needs_user_input: bool = False):
        """Adds a turn to the conversation history and saves project state."""
        if not self.current_project or not self.current_project_state:
            logger.warning("Attempted to add to history with no active project or state.")
            return

        # timestamp = datetime.now().isoformat()
        turn = Turn(sender=sender, message=message, timestamp=self._get_timestamp()) # Corrected: Removed needs_user_input, use self._get_timestamp()
        
        self.current_project_state.conversation_history.append(turn)
        self.current_project_state.last_instruction_sent = message if sender == "GEMINI_MANAGER" else self.current_project_state.last_instruction_sent
        
        # Update pending question based on the 'needs_user_input' flag from Gemini's response processing
        if sender == "GEMINI_MANAGER": # Only Gemini's messages can set a pending question
            self.current_project_state.pending_user_question = message if needs_user_input else None

        save_project_state(self.current_project, self.current_project_state)
        logger.debug(f"Added to history for {self.current_project.name}: [{sender}] - '{message[:50]}...' Needs input: {needs_user_input}")

    def _check_and_run_summarization(self):
        if not self.current_project or not self.current_project_state or not self.gemini_client or not self.config_manager:
            logger.debug("Skipping summarization check: missing project, state, gemini_client or config.")
            return

        interval = self.config_manager.get_summarization_interval()
        history = self.current_project_state.conversation_history
        token_limit = self.config_manager.get_max_context_tokens() # A general token limit to consider

        # Simplistic trigger: if history is long and summary is old or non-existent
        # A more robust approach would involve actual token counting of history vs summary.
        needs_summarization = False
        if not self.current_project_state.current_summary and len(history) > interval // 2:
            needs_summarization = True
            logger.info(f"Triggering summarization: No current summary and history length ({len(history)}) > configured interval/2 ({interval//2}).")
        elif interval > 0 and len(history) > 0 and len(history) % interval == 0: # ensure interval > 0
            needs_summarization = True
            logger.info(f"Triggering summarization: History length ({len(history)}) is a multiple of positive interval ({interval}).")
        elif self.current_project_state.current_summary and interval > 0 and len(history) > (self.current_project_state.last_summary_turn_count + interval):
            needs_summarization = True
            logger.info(f"Triggering summarization: History grew by {interval} turns since last summary (currently {len(history)} turns, last summarized at {self.current_project_state.last_summary_turn_count}).")

        if needs_summarization:
            logger.info(f"Summarization Check: History length {len(history)}, Interval {interval}. Needs summarization: {needs_summarization}")
            
            # Create text from history since last summary
            turns_to_summarize = []
            start_index = self.current_project_state.last_summary_turn_count
            if start_index < 0: start_index = 0 # Should not happen

            for i in range(start_index, len(history)):
                turns_to_summarize.append(history[i])
            
            if not turns_to_summarize:
                logger.info("No new turns to summarize since last summary point.")
                self.current_project_state.last_summary_turn_count = len(history) # Update marker
                save_project_state(self.current_project, self.current_project_state)
                return

            text_to_summarize_parts = [f"Previous Summary (if any):\n{self.current_project_state.current_summary if self.current_project_state.current_summary else 'None'}",
                                       f"\n\nNew conversation turns to incorporate into summary (Goal: {self.current_project.overall_goal}):"]
            for turn in turns_to_summarize:
                text_to_summarize_parts.append(f"[{turn.sender}]: {turn.message}")
            
            full_text_for_gemini = "\n".join(text_to_summarize_parts)
            logger.debug(f"Text for Gemini summarization (first 200 chars): {full_text_for_gemini[:200]}...")

            # Store current state, call Gemini, then restore state or handle new state from Gemini
            # This is a simplified approach. A dedicated summarization state might be better.
            original_state = self.state
            self._set_state(EngineState.RUNNING_CALLING_GEMINI, "Summarizing conversation context...")
            logger.info("--- Calling Gemini for summarization... ---")
            
            try:
                # Use a method in GeminiCommunicator designed for summarization
                # q_summary = queue.Queue() # if making it async, but for now let's do it synchronously for simplicity
                new_summary = self.gemini_client.summarize_conversation_history(
                    history_turns=turns_to_summarize, # Send only new turns
                    existing_summary=self.current_project_state.current_summary,
                    project_goal=self.current_project.overall_goal,
                    max_tokens=self.config_manager.get_max_summary_tokens() # Specific config for summary length
                )

                if new_summary:
                    self.current_project_state.current_summary = new_summary
                    self.current_project_state.last_summary_turn_count = len(history) # Mark how many turns are now summarized
                    self._add_to_history("system", f"Context summarized. New summary (first 100 chars): {new_summary[:100]}...", needs_user_input=False)
                    logger.info(f"--- Context summarization complete. New summary stored. Last summarized turn index: {self.current_project_state.last_summary_turn_count} ---")
                else:
                    logger.warning("Summarization call returned no content. Old summary retained if any.")
                
                save_project_state(self.current_project, self.current_project_state)
            except Exception as e_summary:
                logger.error(f"Error during Gemini summarization call: {e_summary}", exc_info=True)
                self._add_to_history("system", f"Error during context summarization: {e_summary}", needs_user_input=False)
            finally:
                # Restore original state if summarization didn't change it to ERROR or PAUSED
                if self.state == EngineState.RUNNING_CALLING_GEMINI: # If it's still in this temp state
                    self._set_state(original_state, "Summarization attempt finished.")
        else:
            logger.debug(f"Summarization not needed. History length: {len(history)}, Summary exists: {bool(self.current_project_state.current_summary)}, Last summarized turns: {self.current_project_state.last_summary_turn_count}")

    def start_task(self, initial_user_instruction: Optional[str] = None):
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
            
            self._check_and_run_summarization() # Summarize if needed before first Gemini call

            logger.info(f"Calling Gemini with initial instruction for task: '{current_goal[:100]}...'")
            initial_structure_overview = self._get_initial_project_structure_overview()

            gemini_q: queue.Queue[Dict[str, Any]] = queue.Queue()
            self._gemini_call_thread = threading.Thread(
                target=self._call_gemini_in_thread,
                args=(
                    self.current_project.overall_goal,
                    self.current_project_state.conversation_history,
                    self.current_project_state.current_summary,
                    initial_structure_overview, 
                    None, # No cursor log content for the very first call
                    gemini_q
                ),
                daemon=True,
                name="GeminiInitialCallThread"
            )
            self._gemini_call_thread.start()

            try:
                # Timeout for Gemini call completion
                response_data = gemini_q.get(timeout=self.GEMINI_CALL_TIMEOUT_SECONDS) # Use self.
                logger.info(f"Response received from Gemini queue: {response_data.get('status')}")
                if response_data.get("error"):
                    error_msg = response_data["error"]
                    logger.error(f"Gemini call (initial) failed: {error_msg}")
                    self._set_state(EngineState.ERROR, f"Gemini Call Error: {error_msg}")
                else:
                    self._process_gemini_response(response_data)
            except queue.Empty:
                logger.error("Timeout waiting for Gemini response from thread.")
                self._set_state(EngineState.ERROR, "Timeout waiting for Gemini response.")
            except Exception as e:
                error_msg = f"An unexpected error occurred after initial Gemini call: {e}"
                logger.critical(error_msg, exc_info=True)
                self._set_state(EngineState.ERROR, error_msg)

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

    def _call_gemini_in_thread(self, project_goal, full_history, current_summary, initial_structure_overview, cursor_log_content, q):
        try:
            logger.info(f"THREAD {threading.get_ident()}: Calling live Gemini API with history length {len(full_history)}, summary length {len(current_summary) if current_summary else 0}")
            # Ensure all required parameters for get_next_step_from_gemini are fetched and passed
            max_hist_turns = self.config_manager.get_max_history_turns()
            max_ctx_tokens = self.config_manager.get_max_context_tokens()

            response_data = self.gemini_client.get_next_step_from_gemini(
                project_goal=project_goal,
                full_conversation_history=full_history,
                current_context_summary=current_summary,
                max_history_turns=max_hist_turns, # Pass correctly
                max_context_tokens=max_ctx_tokens, # Pass correctly
                cursor_log_content=cursor_log_content,
                initial_project_structure_overview=initial_structure_overview
            )
            q.put(response_data)
        except Exception as e:
            logger.error(f"Exception in _call_gemini_in_thread: {e}", exc_info=True)
            q.put({"status": "ERROR", "content": f"Error in Gemini API call thread: {e}"})

    def _process_gemini_response(self, response_data: Dict[str, Any]):
        with self._engine_lock:
            if self._shutdown_complete or self.state not in [EngineState.RUNNING_CALLING_GEMINI, EngineState.RUNNING_WAITING_INITIAL_GEMINI]:
                logger.warning(f"Engine was stopped, shut down, or in an unexpected state ({self.state.name}) while Gemini response was being processed. Ignoring response: {str(response_data)[:100]}...")
                return

            instruction = response_data.get("instruction")
            next_step = response_data.get("next_step_action") 
            gemini_message_for_history = response_data.get("full_response_for_history")
            if not gemini_message_for_history: # Fallback for older response formats or if missing
                gemini_message_for_history = instruction if instruction else response_data.get("clarification_question", "No specific message from Gemini.")

            logger.info(f"Processing Gemini response. Next step action: '{next_step}'. Instruction provided: {bool(instruction)}")
            logger.debug(f"Full Gemini response for history: {gemini_message_for_history[:200]}...")

            if next_step == "REQUEST_USER_INPUT":
                question = response_data.get("clarification_question", "Gemini needs more information. Please provide input.")
                self._add_to_history("assistant", gemini_message_for_history, needs_user_input=True)
                self._set_state(EngineState.PAUSED_WAITING_USER_INPUT, question)
            elif next_step == "TASK_COMPLETE":
                completion_message = response_data.get("completion_message", "Task marked as complete by Gemini.")
                self._add_to_history("assistant", gemini_message_for_history, needs_user_input=False)
                self._set_state(EngineState.TASK_COMPLETE, completion_message)
                self.stop_file_watcher() 
            elif next_step == "WRITE_TO_FILE" and instruction:
                self._add_to_history("assistant", gemini_message_for_history, needs_user_input=False)
                logger.info(f"Gemini provided instruction. Writing to file... Instruction (first 100 chars): {instruction[:100]}...")
                self._write_instruction_file(instruction)
            else:
                error_msg_detail = response_data.get('error', 'No actionable step or instruction provided by Gemini.')
                error_msg = f"Gemini response was unclear or an error: {error_msg_detail}"
                logger.error(error_msg)
                self._add_to_history("system", f"Error processing Gemini response: {error_msg}", needs_user_input=False)
                self._set_state(EngineState.ERROR, error_msg)
    
    def resume_with_user_input(self, user_response: str):
        with self._engine_lock:
            if self.state != EngineState.PAUSED_WAITING_USER_INPUT:
                logger.warning(f"Cannot resume: Engine not in PAUSED_WAITING_USER_INPUT state (current: {self.state.name}). Input ignored: '{user_response}'")
                return

            if not self.current_project or not self.current_project_state:
                logger.error("Cannot resume: No active project or project state.")
                self._set_state(EngineState.ERROR, "Cannot resume: No active project.")
                return

            logger.info(f"Resuming with user input: '{user_response}'. Calling Gemini...")
            self._add_to_history("user", user_response, needs_user_input=False)
            self.pending_user_question = None # Clear the pending question
            self._set_state(EngineState.RUNNING_CALLING_GEMINI, "Resuming task with user input...")
            
            self._check_and_run_summarization() # Summarize if needed before calling Gemini

            gemini_q: queue.Queue[Dict[str, Any]] = queue.Queue()
            self._gemini_call_thread = threading.Thread(
                target=self._call_gemini_in_thread,
                args=(
                    self.current_project.overall_goal,
                    self.current_project_state.conversation_history,
                    self.current_project_state.current_summary,
                    None, # No initial structure overview for resume
                    None, # No new cursor log at this point
                    gemini_q
                ),
                daemon=True,
                name="GeminiResumeThread"
            )
            self._gemini_call_thread.start()

            try:
                # Timeout for Gemini call completion
                response_data = gemini_q.get(timeout=self.GEMINI_CALL_TIMEOUT_SECONDS) # Use self.
                logger.info(f"Response received from Gemini queue: {response_data.get('status')}")
                if response_data.get("error"):
                    error_msg = response_data["error"]
                    logger.error(f"Gemini call (after user input) failed: {error_msg}")
                    self._set_state(EngineState.ERROR, f"Gemini Call Error: {error_msg}")
                else:
                    self._process_gemini_response(response_data)
            except queue.Empty:
                logger.error("Timeout waiting for Gemini response from thread.")
                self._set_state(EngineState.ERROR, "Timeout waiting for Gemini response.")
            except Exception as e:
                error_msg = f"Unexpected error after Gemini call (resume): {e}"
                logger.critical(error_msg, exc_info=True)
                self._set_state(EngineState.ERROR, error_msg)

    def stop_current_task_gracefully(self):
        with self._engine_lock:
            if not self.current_project or not self.current_project_state:
                logger.info("No active project or task to stop.")
                # Set to IDLE if no project, or PROJECT_SELECTED if project exists but no task was active.
                self._set_state(EngineState.IDLE if not self.current_project else EngineState.PROJECT_SELECTED, "Stop command received with no active task.")
                return

            logger.info(f"Attempting to gracefully stop task for project: {self.current_project.name} from state {self.state.name}")

            if self._cursor_timeout_timer and self._cursor_timeout_timer.is_alive():
                self._cancel_cursor_timeout()

            self.stop_file_watcher()

            if self._gemini_call_thread and self._gemini_call_thread.is_alive():
                logger.warning("A Gemini call is in progress. It will be allowed to complete, but its results should be ignored by _process_gemini_response due to state change.")
                # The _process_gemini_response method checks self._shutdown_complete and state.

            self.current_project_state.current_task_goal = None 
            self._add_to_history("system", f"Task stopped by user from state {self.state.name}.", needs_user_input=False)
            self._set_state(EngineState.PROJECT_SELECTED, "Task stopped by user.") 
            logger.info(f"Task stopped for {self.current_project.name}. Engine is now in {self.state.name} state.")

    def _start_cursor_timeout(self):
        with self._engine_lock:
            self._cancel_cursor_timeout() 
            if self.state == EngineState.RUNNING_WAITING_LOG: 
                timeout_seconds = self.config_manager.get_cursor_log_timeout_seconds()
                self._cursor_timeout_timer = threading.Timer(timeout_seconds, self._handle_cursor_timeout)
                self._cursor_timeout_timer.daemon = True
                self._cursor_timeout_timer.start()
                logger.info(f"Cursor log timeout started ({timeout_seconds}s).")
            else:
                logger.debug(f"Cursor timeout not started. Engine state is {self.state.name}, not RUNNING_WAITING_LOG.")

    def _cancel_cursor_timeout(self):
        with self._engine_lock:
            if self._cursor_timeout_timer and self._cursor_timeout_timer.is_alive():
                self._cursor_timeout_timer.cancel()
                logger.info("Cursor log timeout cancelled.")
            self._cursor_timeout_timer = None

    def _handle_cursor_timeout(self):
        with self._engine_lock:
            if self.state == EngineState.RUNNING_WAITING_LOG:
                error_msg = f"Cursor log timeout: No log file ('{self.config_manager.get_cursor_output_filename()}') received from Cursor agent within {self.config_manager.get_cursor_log_timeout_seconds()} seconds."
                logger.error(error_msg)
                self._add_to_history("system", error_msg, needs_user_input=False)
                
                # Stop watcher, it failed to see the file.
                self.stop_file_watcher()

                logger.info("Cursor log timed out. Asking Gemini for next step...")
                self._set_state(EngineState.RUNNING_CALLING_GEMINI, "Cursor log timed out. Asking Gemini for next step...")

                if not self.current_project or not self.current_project_state: # Should not happen here
                    logger.critical("Critical: No project/state during cursor timeout handling.")
                    self._set_state(EngineState.ERROR, "Internal error during cursor timeout handling.")
                    return

                gemini_q: queue.Queue[Dict[str, Any]] = queue.Queue()
                self._gemini_call_thread = threading.Thread(
                    target=self._call_gemini_in_thread,
                    args=(
                        self.current_project.overall_goal,
                        self.current_project_state.conversation_history, 
                        self.current_project_state.current_summary,
                        f"Context: Cursor agent did not produce a log file ('{self.config_manager.get_cursor_output_filename()}') in the expected time. What should be the next step? Consider if retrying, stopping, or asking user is appropriate.",
                        None, # No new cursor log content
                        gemini_q
                    ),
                    daemon=True,
                    name="GeminiCursorTimeoutHandlerThread"
                )
                self._gemini_call_thread.start()

                try:
                    # Timeout for Gemini call completion
                    response_data = gemini_q.get(timeout=self.GEMINI_CALL_TIMEOUT_SECONDS) # Use self.
                    logger.info(f"Response received from Gemini queue: {response_data.get('status')}")
                    if response_data.get("error"):
                        err = response_data["error"]
                        logger.error(f"Gemini call (after cursor timeout) failed: {err}")
                        self._set_state(EngineState.ERROR, f"Gemini Call Error (after timeout): {err}")
                    else:
                        self._process_gemini_response(response_data)
                except queue.Empty:
                    err = "Gemini call (after cursor timeout) timed out itself."
                    logger.error(err)
                    self._set_state(EngineState.ERROR, f"Gemini Timeout (after cursor timeout): {err}")
                except Exception as e:
                    err = f"Unexpected error after Gemini call (cursor timeout): {e}"
                    logger.critical(err, exc_info=True)
                    self._set_state(EngineState.ERROR, err)
            else:
                logger.info(f"Cursor timeout handler triggered, but state is {self.state.name} (not RUNNING_WAITING_LOG). No action taken.")
            self._cursor_timeout_timer = None

    def shutdown(self):
        logger.info("OrchestrationEngine shutdown sequence initiated...")
        with self._engine_lock:
            if self._shutdown_complete:
                logger.info("Engine shutdown already completed or in progress.")
                return
            self._shutdown_complete = True # Mark immediately to prevent re-entry
            
            current_state_before_shutdown = self.state.name
            logger.info(f"Engine state before shutdown: {current_state_before_shutdown}")

            logger.info("Cancelling any pending cursor timeout...")
            self._cancel_cursor_timeout()

            logger.info("Stopping file watcher if active...")
            self.stop_file_watcher()

            # Thread joining for Gemini thread is tricky. Daemon threads should exit.
            # We set _shutdown_complete, and _process_gemini_response checks this.
            if self._gemini_call_thread and self._gemini_call_thread.is_alive():
                logger.info(f"Gemini call thread '{self._gemini_call_thread.name}' is alive. It will be allowed to complete as a daemon thread or its results ignored.")
                # No join here to prevent blocking shutdown, rely on daemon and _shutdown_complete flag.

            if self.current_project and self.current_project_state:
                logger.info(f"Saving final state for project: {self.current_project.name} with status {self.state.name}")
                try:
                    # Explicitly set current_status to a more stable state if it was running
                    if self.state.name.startswith("RUNNING_"):
                        logger.warning(f"Engine was in a RUNNING state ({self.state.name}) during shutdown. Saving project state as PROJECT_SELECTED.")
                        self.current_project_state.current_status = EngineState.PROJECT_SELECTED.name
                    elif self.state == EngineState.PAUSED_WAITING_USER_INPUT:
                         logger.info("Engine was PAUSED_WAITING_USER_INPUT during shutdown. Preserving this state for project.")
                         self.current_project_state.current_status = EngineState.PAUSED_WAITING_USER_INPUT.name
                    else:
                        self.current_project_state.current_status = self.state.name # Persist the actual final state (IDLE, ERROR, TASK_COMPLETE, etc)
                    save_project_state(self.current_project, self.current_project_state)
                except PersistenceError as e:
                    logger.error(f"Error saving project state for {self.current_project.name} during shutdown: {e}", exc_info=True)
            
            self.state = EngineState.IDLE # Final engine state after shutdown process
            logger.info(f"OrchestrationEngine shutdown complete. Final engine state: {self.state.name}.")

    def _load_gemini_comms_and_client(self):
        logger.info("Attempting to load gemini_comms module and initialize client...")
        print("DEBUG Engine._load_gemini_comms_and_client: Start", file=sys.stderr)
        
        importlib.invalidate_caches()
        logger.debug("Called importlib.invalidate_caches()")
        print("DEBUG Engine._load_gemini_comms_and_client: Called importlib.invalidate_caches()", file=sys.stderr)

        module_to_load_name = None
        mock_comms_path = Path("gemini_comms_mock.py")
        real_comms_module_name = "gemini_comms_real" # The .py is assumed by importlib

        if mock_comms_path.exists() and mock_comms_path.is_file():
            logger.info(f"Mock comms file '{mock_comms_path}' exists. Attempting to load it.")
            print(f"DEBUG Engine._load_gemini_comms_and_client: Mock file '{mock_comms_path}' exists.", file=sys.stderr)
            module_to_load_name = "gemini_comms_mock" 
        else:
            logger.info(f"Mock comms file '{mock_comms_path}' does not exist. Attempting to load real comms module '{real_comms_module_name}'.")
            print(f"DEBUG Engine._load_gemini_comms_and_client: Mock file '{mock_comms_path}' does NOT exist. Using real: {real_comms_module_name}", file=sys.stderr)
            module_to_load_name = real_comms_module_name
        
        if module_to_load_name in sys.modules:
            del sys.modules[module_to_load_name]
            logger.info(f"Removed '{module_to_load_name}' from sys.modules before loading.")
            print(f"DEBUG Engine._load_gemini_comms_and_client: Removed '{module_to_load_name}' from sys.modules.", file=sys.stderr)

        try:
            # Dynamically import the chosen module
            self.gemini_comms_module = importlib.import_module(module_to_load_name)
            logger.info(f"Successfully loaded module: '{module_to_load_name}' from {getattr(self.gemini_comms_module, '__file__', 'N/A')}")
            print(f"DEBUG Engine._load_gemini_comms_and_client: Loaded module '{module_to_load_name}' from {getattr(self.gemini_comms_module, '__file__', 'N/A')}", file=sys.stderr)

            if hasattr(self.gemini_comms_module, 'GeminiCommunicator'):
                self.gemini_client = self.gemini_comms_module.GeminiCommunicator()
                client_type = type(self.gemini_client).__name__
                client_module = type(self.gemini_client).__module__
                logger.info(f"GeminiCommunicator instance created. Type: {client_module}.{client_type}")
                print(f"DEBUG Engine._load_gemini_comms_and_client: GeminiCommunicator type: {client_module}.{client_type}", file=sys.stderr)
                
                # More robust check for mock: check module name or a specific attribute unique to mock
                if module_to_load_name == "gemini_comms_mock" or hasattr(self.gemini_client, 'mock_type'):
                    mock_type_attr = getattr(self.gemini_client, 'mock_type', 'N/A')
                    logger.info(f"Loaded GeminiCommunicator is a MOCK from '{module_to_load_name}'. Mock type attribute: {mock_type_attr}")
                    print(f"DEBUG Engine._load_gemini_comms_and_client: Detected MOCK client. Module: '{module_to_load_name}', Mock type attr: {mock_type_attr}", file=sys.stderr)
                else:
                    logger.info(f"Loaded GeminiCommunicator is REAL from '{module_to_load_name}'.")
                    print(f"DEBUG Engine._load_gemini_comms_and_client: Detected REAL client from '{module_to_load_name}'.", file=sys.stderr)
            else:
                logger.error(f"Module '{module_to_load_name}' loaded, but GeminiCommunicator class not found.")
                print(f"DEBUG Engine._load_gemini_comms_and_client: GeminiCommunicator class not found in module '{module_to_load_name}'.", file=sys.stderr)
                self.gemini_client = None
        except ImportError as ie:
            logger.error(f"ImportError loading module '{module_to_load_name}': {ie}. This might be critical if it's the real module.", exc_info=True)
            print(f"DEBUG Engine._load_gemini_comms_and_client: ImportError for '{module_to_load_name}': {ie}", file=sys.stderr)
            # If mock fails to load, we might want to try loading real one as a fallback, but current logic loads real if mock is absent.
            # If real one fails here, it's a problem.
            self.gemini_comms_module = None
            self.gemini_client = None
        except Exception as e:
            logger.error(f"Error during dynamic import or instantiation of GeminiCommunicator from '{module_to_load_name}': {e}", exc_info=True)
            print(f"DEBUG Engine._load_gemini_comms_and_client: Exception for '{module_to_load_name}': {e}", file=sys.stderr)
            self.gemini_comms_module = None
            self.gemini_client = None
        
        if not self.gemini_client:
            logger.warning("Gemini client (self.gemini_client) is None after _load_gemini_comms_and_client attempt.")
            print("DEBUG Engine._load_gemini_comms_and_client: self.gemini_client is None at end of method.", file=sys.stderr)

    def reinitialize_gemini_client(self):
        logger.info("Attempting to re-initialize Gemini client by reloading comms module logic...")
        print("DEBUG Engine.reinitialize_gemini_client: Start", file=sys.stderr)
        
        self.gemini_comms_module = None # Reset the stored module object
        self.gemini_client = None # Reset the client

        self._load_gemini_comms_and_client() # Perform a fresh load
        
        if self.gemini_client:
            logger.info("Gemini client re-initialization attempt complete. Client is now set.")
            print("DEBUG Engine.reinitialize_gemini_client: Client re-initialization complete, client is set.", file=sys.stderr)
            
            # Check if the re-initialized client is mock or real
            client_module_name = getattr(getattr(self.gemini_client, '__module__', None), 'split', lambda x: [''])('.')[-1]
            if client_module_name == "gemini_comms_mock" or hasattr(self.gemini_client, 'mock_type'):
                mock_type_attr = getattr(self.gemini_client, 'mock_type', 'N/A')
                logger.info(f"Re-initialized GeminiCommunicator is a MOCK. Module: '{client_module_name}', Mock type attribute: {mock_type_attr}")
                print(f"DEBUG Engine.reinitialize_gemini_client: Detected MOCK client. Module: '{client_module_name}', Mock type attr: {mock_type_attr}", file=sys.stderr)
            else:
                logger.info(f"Re-initialized GeminiCommunicator is REAL from module '{client_module_name}'.")
                print(f"DEBUG Engine.reinitialize_gemini_client: Detected REAL client from '{client_module_name}'.", file=sys.stderr)
            return True
        else:
            logger.error("Gemini client is None after re-initialization attempt.")
            print("DEBUG Engine.reinitialize_gemini_client: Client is None after re-initialization.", file=sys.stderr)
            return False

    def get_project_path(self):
        # This method seems unused and current_project might not have a direct 'path' attribute.
        # It likely intended to return current_project.workspace_root_path
        if self.current_project:
            return self.current_project.workspace_root_path
        return None

    def get_current_engine_state_name(self): # Renamed for clarity
        return self.state.name

class LogFileCreatedHandler(FileSystemEventHandler): # type: ignore
    def __init__(self, engine: 'OrchestrationEngine'):
        super().__init__()
        self.engine = engine
        self.last_event_time: Dict[str, float] = {}
        self.debounce_seconds = 2.0 # Configurable debounce window
        if not self.engine.config_manager:
            logger.error("LogFileCreatedHandler initialized without engine.config_manager! Using default debounce.")
        else:
            self.debounce_seconds = self.engine.config_manager.get_watchdog_debounce_seconds()

    def on_created(self, event):
        if not isinstance(event, FileCreatedEvent):
            return # Only interested in file creation events

        log_file_path = event.src_path
        log_file_name = os.path.basename(log_file_path)
        log_file_dir = os.path.dirname(log_file_path)

        logger.debug(f"Watchdog event: File created '{log_file_name}' in '{log_file_dir}'. Event type: {type(event)}")

        # Debounce
        current_time = time.monotonic()
        if log_file_path in self.last_event_time and \
           (current_time - self.last_event_time[log_file_path]) < self.debounce_seconds:
            logger.debug(f"Debounced duplicate create event for: {log_file_path}")
            return
        self.last_event_time[log_file_path] = current_time

        if not self.engine.config_manager:
            logger.error("LogFileCreatedHandler: engine.config_manager is None. Cannot get target filename. Ignoring event for {log_file_name}")
            return
            
        target_log_filename = self.engine.config_manager.get_cursor_output_filename()
        if log_file_name != target_log_filename:
            logger.debug(f"Ignoring file '{log_file_name}'; does not match target '{target_log_filename}'.")
            return
        
        # Check if the file is in the expected dev_logs_dir (not in 'processed' or other subdirs)
        # self.engine.dev_logs_dir should be the absolute path to the NON-PROCESSED logs dir.
        if os.path.abspath(log_file_dir) != os.path.abspath(self.engine.dev_logs_dir):
            logger.debug(f"Ignoring file '{log_file_name}' in '{log_file_dir}'. Expected in '{self.engine.dev_logs_dir}'.")
            return

        if self.engine.state != EngineState.RUNNING_WAITING_LOG:
            logger.warning(f"LogFileHandler: File '{log_file_path}' created, but engine not in RUNNING_WAITING_LOG (state: {self.engine.state.name}). This might be a late event or an issue.")
            # Decide if to process anyway or strictly ignore. For now, strict ignore.
            return

        logger.info(f"LogFileHandler: Detected target log file: {log_file_path}")
        # Run _on_log_file_created in a new thread to avoid blocking watchdog
        handler_thread = threading.Thread(target=self.engine._on_log_file_created, args=(log_file_path,), daemon=True)
        handler_thread.name = f"LogCreatedHandlerThread-{os.path.basename(log_file_path)}"
        handler_thread.start()

# Removed dummy_gui_callback and if __name__ == '__main__' block for OrchestrationEngine
# This module is intended to be imported, not run directly as the main script.
