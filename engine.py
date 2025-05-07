import os
import time
import shutil
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Callable, List, Dict, Any

from .models import Project, ProjectState
from .persistence import load_project_state, save_project_state, get_project_by_id, load_projects
from .gemini_comms import GeminiCommunicator
from .config_manager import ConfigManager

# Watchdog is an external dependency, ensure it's handled if not available
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    Observer = None
    FileSystemEventHandler = None
    print("WARNING (Engine): watchdog library not found. File watching will be disabled.")

class EngineState(Enum):
    IDLE = auto()
    PROJECT_SELECTED = auto() # Ready to start a task for the selected project
    RUNNING_WAITING_INITIAL_GEMINI = auto() # After user starts, before first Gemini call
    RUNNING_WAITING_LOG = auto() # Instruction sent to Cursor, waiting for cursor_step_output.txt
    RUNNING_PROCESSING_LOG = auto() # Log received, about to call Gemini
    RUNNING_CALLING_GEMINI = auto() # Calling Gemini API
    PAUSED_WAITING_USER_INPUT = auto() # Gemini requested user input
    TASK_COMPLETE = auto() # Gemini reported TASK_COMPLETE
    ERROR = auto()

class OrchestrationEngine:
    def __init__(self, gui_update_callback: Optional[Callable[[str, Any], None]] = None):
        self.current_project: Optional[Project] = None
        self.current_project_state: Optional[ProjectState] = None
        self.state: EngineState = EngineState.IDLE
        self.gemini_comms = GeminiCommunicator() # Might raise if config is bad
        self.config_manager = ConfigManager()
        self.gui_update_callback = gui_update_callback # To notify GUI of changes
        self.file_observer: Optional[Observer] = None
        self.file_event_handler: Optional[LogFileHandler] = None
        self.dev_logs_dir: str = ""
        self.dev_instructions_dir: str = ""
        self.last_error_message: Optional[str] = None

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
        if self.state not in [EngineState.IDLE, EngineState.PROJECT_SELECTED, EngineState.TASK_COMPLETE, EngineState.ERROR]:
            self._set_state(EngineState.ERROR, "Cannot change project while a task is active or paused.")
            return False
        
        self.current_project = project
        project_state = load_project_state(project)
        if not project_state:
            self._set_state(EngineState.ERROR, f"Failed to load state for project: {project.name}")
            self.current_project = None # Clear project if state fails
            return False
        
        self.current_project_state = project_state
        self.dev_logs_dir = os.path.join(project.workspace_root_path, self.config_manager.get_default_dev_logs_dir())
        self.dev_instructions_dir = os.path.join(project.workspace_root_path, self.config_manager.get_default_dev_instructions_dir())
        
        # Ensure directories exist for the project
        os.makedirs(self.dev_logs_dir, exist_ok=True)
        os.makedirs(os.path.join(self.dev_logs_dir, "processed"), exist_ok=True)
        os.makedirs(self.dev_instructions_dir, exist_ok=True)
        
        self._set_state(EngineState.PROJECT_SELECTED)
        self._notify_gui("project_loaded", {
            "project_name": project.name,
            "goal": project.overall_goal,
            "history": self.current_project_state.conversation_history,
            "status": self.current_project_state.current_status
        })
        print(f"Active project set to: {project.name}")
        # Attempt to sync engine state with loaded project state
        try:
            loaded_engine_state = EngineState[self.current_project_state.current_status]
            if loaded_engine_state == EngineState.PAUSED_WAITING_USER_INPUT or loaded_engine_state == EngineState.ERROR: # States we can resume from or reflect
                 self._set_state(loaded_engine_state)
            elif loaded_engine_state != EngineState.IDLE and loaded_engine_state != EngineState.PROJECT_SELECTED:
                 # If it was running, set to idle or error, as we can't resume a running state directly on load
                 self._set_state(EngineState.PROJECT_SELECTED) # Or ERROR with a message
                 self.current_project_state.current_status = EngineState.PROJECT_SELECTED.name
                 save_project_state(self.current_project, self.current_project_state)

        except KeyError:
            print(f"Warning: Loaded project state '{self.current_project_state.current_status}' is not a valid EngineState. Resetting to PROJECT_SELECTED.")
            self._set_state(EngineState.PROJECT_SELECTED)
            self.current_project_state.current_status = EngineState.PROJECT_SELECTED.name
            save_project_state(self.current_project, self.current_project_state)

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

        self.file_event_handler = LogFileHandler(self._on_log_file_created)
        self.file_observer = Observer()
        try:
            # Ensure the directory exists before watching
            os.makedirs(self.dev_logs_dir, exist_ok=True)
            self.file_observer.schedule(self.file_event_handler, self.dev_logs_dir, recursive=False)
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
                self.file_event_handler = None

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

        gemini_status, gemini_content = self.gemini_comms.get_next_step_from_gemini(
            project_goal=self.current_project.overall_goal,
            context_summary=self.current_project_state.context_summary, # Placeholder
            recent_history=self.current_project_state.conversation_history,
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
            entry = {"sender": sender, "timestamp": datetime.now().isoformat(), "message": message}
            self.current_project_state.conversation_history.append(entry)
            self._notify_gui("new_message", entry)
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

        gemini_status, gemini_content = self.gemini_comms.get_next_step_from_gemini(
            project_goal=self.current_project.overall_goal,
            context_summary=self.current_project_state.context_summary, # Placeholder
            recent_history=self.current_project_state.conversation_history, # May include initial_user_instruction
            cursor_log_content=None # No log from Cursor initially
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

        gemini_status, gemini_content = self.gemini_comms.get_next_step_from_gemini(
            project_goal=self.current_project.overall_goal,
            context_summary=self.current_project_state.context_summary,
            recent_history=self.current_project_state.conversation_history,
            cursor_log_content=None # No new log from Cursor, Gemini is responding to user input
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
    class LogFileHandler(FileSystemEventHandler):
        def __init__(self, callback: Callable[[str], None]):
            super().__init__()
            self.callback = callback

        def on_created(self, event):
            if not event.is_directory and event.src_path.endswith("cursor_step_output.txt"):
                # Debounce or delay slightly in case of rapid partial writes, though watchdog often handles this.
                # time.sleep(0.1) # Optional: small delay
                self.callback(event.src_path)
        
        def on_modified(self, event):
            # Sometimes files are created empty then modified, or modified multiple times rapidly.
            # on_created is often more reliable for "new file appeared".
            # If on_created is missed, on_modified could be a fallback but needs careful handling to avoid multiple triggers.
            if not event.is_directory and event.src_path.endswith("cursor_step_output.txt"):
                # Check if the engine is expecting it, to avoid processing if it was an on_created event handled already
                # This might be complex. For simplicity, relying on on_created for now.
                # print(f"LogFileHandler: Detected modification of {event.src_path}")
                pass 
else:
    LogFileHandler = None # Placeholder if watchdog not imported

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
            if LogFileHandler: # Check if class is defined
                engine._on_log_file_created(log_file) # Manually call for testing flow
            else:
                print("Watchdog not available, manual trigger of _on_log_file_created needed for full test.")

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
                if LogFileHandler:
                    engine._on_log_file_created(second_log_file)
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