# cursor_bridge.py
import json
import os
import time
import datetime
import shutil # For moving processed tasks
import shlex # Moved from process_task
import traceback # Ensure it's at top level

RTH_SCRIPT_PATH_FOR_AGENT = r"C:\Users\Man\CursorAgentUtils\robust_terminal_handler.py"
PYTHON_EXE_FOR_RTH_FOR_AGENT = r"C:\Users\Man\AutoAgent\.venv\Scripts\python.exe"

TASK_QUEUE_FILE = "task_queue.json"
INSTRUCTIONS_DIR = "instructions" # For per-task output, status, error
ARCHIVE_DIR = os.path.join(INSTRUCTIONS_DIR, "archive")
PROCESSED_DIR = os.path.join(ARCHIVE_DIR, "processed")
FAILED_DIR = os.path.join(ARCHIVE_DIR, "failed")

POLL_INTERVAL_SECONDS = 15

def ensure_dirs():
    for dir_path in [INSTRUCTIONS_DIR, ARCHIVE_DIR, PROCESSED_DIR, FAILED_DIR]:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
            log_message(None, f"Created directory: {dir_path}")

def log_message(task_id, message, level="INFO"):
    """Appends a message to a general or task-specific log and prints it."""
    timestamp = datetime.datetime.now().isoformat()
    log_entry = f"{timestamp} - {level} - {message}\n"
    
    general_log_file = os.path.join(INSTRUCTIONS_DIR, "cursor_bridge.log")
    try:
        with open(general_log_file, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception as e:
        print(f"CRITICAL: Error writing to general log {general_log_file}: {e}")

    if task_id:
        task_log_file = os.path.join(INSTRUCTIONS_DIR, f"{task_id}.output.log")
        try:
            with open(task_log_file, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception as e:
            print(f"CRITICAL: Error writing to task log {task_log_file}: {e}")
    
    print(f"BRIDGE_LOG ({task_id if task_id else 'GENERAL'}): {message}")


def update_task_status_in_queue(task_id, new_status, notes="", agent_action_details=None):
    """Updates the status of a task in task_queue.json."""
    try:
        with open(TASK_QUEUE_FILE, "r+", encoding="utf-8") as f:
            tasks_data = json.load(f) # Changed variable name for clarity
            task_found = False
            for task in tasks_data.get("tasks", []): # MODIFIED THIS LINE
                if task.get("task_id") == task_id:
                    task["status"] = new_status
                    task["last_updated"] = datetime.datetime.now().isoformat()
                    if notes:
                        task["notes"] = notes
                    if agent_action_details: # New: Store action details for agent
                        task["agent_action_details"] = agent_action_details
                    else: # Ensure it's removed if not applicable for this status update
                        task.pop("agent_action_details", None)
                    task_found = True
                    break
            if task_found:
                f.seek(0)
                json.dump(tasks_data, f, indent=4) # Ensure dumping the whole object
                f.truncate()
                log_message(task_id, f"Status in {TASK_QUEUE_FILE} updated to {new_status}.")
                return True
            else:
                log_message(task_id, f"Task ID {task_id} not found in {TASK_QUEUE_FILE} for status update.")
                return False
    except Exception as e:
        log_message(task_id, f"Error updating status in {TASK_QUEUE_FILE}: {e}", level="ERROR")
        return False

def write_task_error_file(task_id, error_type, message, traceback_str="", retry_flag=True, suggested_clarification=""):
    """Writes error details to a task-specific error.json."""
    error_data = {
        "task_id": task_id,
        "error_type": error_type,
        "message": message,
        "traceback": traceback_str,
        "retry_flag": retry_flag,
        "suggested_clarification": suggested_clarification,
        "timestamp": datetime.datetime.now().isoformat()
    }
    error_file_path = os.path.join(INSTRUCTIONS_DIR, f"{task_id}.error.json")
    try:
        with open(error_file_path, "w", encoding="utf-8") as f:
            json.dump(error_data, f, indent=4)
        log_message(task_id, f"Error file created: {error_file_path}", level="ERROR")
    except Exception as e:
        log_message(task_id, f"Error writing error file {error_file_path}: {e}", level="CRITICAL")

def archive_task_files(task_id, outcome_dir):
    """Moves task-specific files to the archive and cleans up task-specific instruction directory."""
    log_message(task_id, f"Archiving files for task {task_id} to {outcome_dir}.")
    
    # Move primary log files
    for ext in [".output.log", ".error.json"]:
        src_file = os.path.join(INSTRUCTIONS_DIR, f"{task_id}{ext}")
        if os.path.exists(src_file):
            try:
                # Ensure the specific outcome_dir (processed/failed) exists before moving
                specific_outcome_dir = os.path.join(ARCHIVE_DIR, outcome_dir) # outcome_dir is "processed" or "failed"
                if not os.path.exists(specific_outcome_dir):
                    os.makedirs(specific_outcome_dir)
                    log_message(task_id, f"Created archive subdirectory: {specific_outcome_dir}", level="DEBUG")

                shutil.move(src_file, os.path.join(specific_outcome_dir, f"{task_id}{ext}"))
                log_message(task_id, f"Moved {src_file} to archive: {specific_outcome_dir}.")
            except Exception as e:
                log_message(task_id, f"Failed to move {src_file} to archive: {e}", level="WARNING")

    # Attempt to remove task-specific working subdirectory in INSTRUCTIONS_DIR if it exists
    # e.g., instructions/task-001/ which might contain RTH outputs from agent
    task_specific_instruction_subdir = os.path.join(INSTRUCTIONS_DIR, task_id)
    if os.path.isdir(task_specific_instruction_subdir):
        try:
            shutil.rmtree(task_specific_instruction_subdir)
            log_message(task_id, f"Successfully removed task-specific instruction subdirectory: {task_specific_instruction_subdir}")
        except Exception as e:
            log_message(task_id, f"Failed to remove task-specific instruction subdirectory {task_specific_instruction_subdir}: {e}", level="WARNING")
    else:
        log_message(task_id, f"No task-specific instruction subdirectory found at {task_specific_instruction_subdir} to remove.", level="DEBUG")


def process_task(task_data):
    """Processes a single task from the task_queue."""
    task_id = task_data.get("task_id", f"unknown_task_{int(time.time())}")
    objective = task_data.get("objective", "No objective provided.")
    instruction_details = task_data.get("instruction_details", {})

    log_message(task_id, f"Starting task: {objective}")
    update_task_status_in_queue(task_id, "in_progress")

    try:
        # --- Task Validation ---
        log_message(task_id, "Validating task parameters...")
        if not instruction_details: # Check if instruction_details is empty
            error_msg = "Task has no instruction_details."
            log_message(task_id, error_msg, level="ERROR")
            write_task_error_file(task_id, "ValidationError", error_msg, "", False, "Task instruction_details cannot be empty.")
            update_task_status_in_queue(task_id, "failed", error_msg)
            archive_task_files(task_id, "failed")
            return # Stop processing this task

        files_to_modify = instruction_details.get("files_to_modify", [])
        commands_to_execute = instruction_details.get("commands_to_execute", [])

        if not files_to_modify and not commands_to_execute:
            log_message(task_id, "Task has no files_to_modify and no commands_to_execute specified.", level="WARNING")
            # If no actions, consider it completed if it's just a placeholder/info task.
            # Or, it could be an error if actions are always expected.
            # For now, let it proceed to the end, where it will be marked completed.
            # This will be handled by the final status update to 'completed' if no errors occur.

        for i, file_op in enumerate(files_to_modify):
            if not file_op.get("file_path") or not file_op.get("action"):
                error_msg = f"File operation at index {i} is missing 'file_path' or 'action'."
                log_message(task_id, error_msg, level="ERROR")
                write_task_error_file(task_id, "ValidationError", error_msg, "", False, "Ensure file_path and action are specified for all file operations.")
                update_task_status_in_queue(task_id, "failed", error_msg)
                archive_task_files(task_id, "failed")
                return

        for i, cmd_op in enumerate(commands_to_execute):
            if not cmd_op.get("command_string"):
                error_msg = f"Command operation at index {i} is missing 'command_string'."
                log_message(task_id, error_msg, level="ERROR")
                write_task_error_file(task_id, "ValidationError", error_msg, "", False, "Ensure command_string is specified for all command operations.")
                update_task_status_in_queue(task_id, "failed", error_msg)
                archive_task_files(task_id, "failed")
                return
        log_message(task_id, "Task parameters validated.")

        # --- File Modifications (Delegated to Agent) ---
        if files_to_modify:
            log_message(task_id, "Preparing file modifications for agent delegation...")
            all_file_ops_locally_validated = True # Before delegation
            for file_op in files_to_modify:
                f_path = file_op.get('file_path')
                action = file_op.get('action')
                # Get all potential fields first
                content = file_op.get('content')
                content_to_insert_val = file_op.get('content_to_insert')
                replacement_content_val = file_op.get('replacement_content')
                start_line = file_op.get('start_line_number') # RESTORED
                end_line = file_op.get('end_line_number')     # RESTORED
                line_marker = file_op.get('line_marker')
                create_if_not_exists = file_op.get('create_if_not_exists', False)

                # Default content_str, primarily for create_file and replace_content
                # Other actions will use their specific content fields
                current_content_str = ""
                if isinstance(content, list):
                    current_content_str = "\n".join(content)
                elif content is not None:
                    current_content_str = content
                # else: current_content_str remains ""

                action_params = None
                try:
                    # Perform pre-delegation local checks
                    if action not in ["create_file", "replace_content", "append_content"] and not os.path.exists(f_path) and not create_if_not_exists:
                        # For insert, replace_lines, delete_lines on non-existent file without create_if_not_exists flag
                         raise FileNotFoundError(f"File '{f_path}' not found, and create_if_not_exists is false. Action '{action}' cannot proceed locally and will likely fail for agent if not handled.")

                    if action == "create_file":
                        action_params = {
                            "tool_to_call": "edit_file", "target_file": f_path, "action_type": action,
                            "code_edit": current_content_str, # Uses general 'content'
                            "instructions": f"Create file {f_path} with specified content. If it exists, it will be overwritten."
                        }
                    elif action == "replace_content":
                        if not os.path.exists(f_path) and not create_if_not_exists:
                             raise FileNotFoundError(f"File '{f_path}' not found for replace_content and create_if_not_exists is false.")
                        action_params = {
                            "tool_to_call": "edit_file", "target_file": f_path, "action_type": action,
                            "code_edit": current_content_str, # Uses general 'content'
                            "create_if_not_exists": create_if_not_exists,
                            "instructions": f"Replace entire content of file {f_path}. If create_if_not_exists was true ({create_if_not_exists}) and file didn't exist, this will create it."
                        }
                    elif action == "append_content":
                        # append_content uses 'content_to_append' in its agent_action_details,
                        # but the task instruction_details puts it in 'content'
                        action_params = {
                            "tool_to_call": "edit_file", "target_file": f_path, "action_type": action,
                            "content_to_append": current_content_str,  # Uses general 'content' from task, mapped to 'content_to_append' for agent
                            "create_if_not_exists": create_if_not_exists,
                            "instructions": f"Append the provided 'content_to_append' to file '{f_path}'. If file exists, read current content and append new content (ensure newline if needed). If file doesn't exist and create_if_not_exists is true ({create_if_not_exists}), create it with 'content_to_append'."
                        }
                    elif action == "insert_after_line":
                        if not line_marker: raise ValueError(f"Action '{action}' requires 'line_marker'.")
                        if not os.path.exists(f_path) and not create_if_not_exists: raise FileNotFoundError(f"File '{f_path}' not found for {action}.")
                        
                        insert_str = ""
                        if isinstance(content_to_insert_val, list):
                            insert_str = "\n".join(content_to_insert_val)
                        elif content_to_insert_val is not None:
                            insert_str = content_to_insert_val

                        action_params = {
                            "tool_to_call": "edit_file", "target_file": f_path, "action_type": action,
                            "line_marker": line_marker, "content_to_insert": insert_str, # Uses specific 'content_to_insert_val'
                            "create_if_not_exists": create_if_not_exists,
                            "instructions": f"In file '{f_path}', insert 'content_to_insert' after line with '{line_marker}'. If create_if_not_exists ({create_if_not_exists}) and file missing, agent may need to create it first (empty or with marker) if feasible for this action."
                        }
                    elif action == "insert_before_line":
                        if not line_marker: raise ValueError(f"Action '{action}' requires 'line_marker'.")
                        if not os.path.exists(f_path) and not create_if_not_exists: raise FileNotFoundError(f"File '{f_path}' not found for {action}.")

                        insert_bf_str = ""
                        if isinstance(content_to_insert_val, list):
                            insert_bf_str = "\n".join(content_to_insert_val)
                        elif content_to_insert_val is not None:
                            insert_bf_str = content_to_insert_val
                        
                        action_params = {
                            "tool_to_call": "edit_file", "target_file": f_path, "action_type": action,
                            "line_marker": line_marker, "content_to_insert": insert_bf_str, # Uses specific 'content_to_insert_val'
                            "create_if_not_exists": create_if_not_exists,
                            "instructions": f"In file '{f_path}', insert 'content_to_insert' before line with '{line_marker}'. If create_if_not_exists ({create_if_not_exists}) and file missing, agent may need to create it first."
                        }
                    elif action == "replace_lines":
                        if start_line is None or end_line is None: raise ValueError(f"Action '{action}' requires 'start_line_number' and 'end_line_number'.")
                        if not os.path.exists(f_path) and not create_if_not_exists: raise FileNotFoundError(f"File '{f_path}' not found for {action}.")
                        
                        replace_str = ""
                        if isinstance(replacement_content_val, list):
                            replace_str = "\n".join(replacement_content_val)
                        elif replacement_content_val is not None:
                            replace_str = replacement_content_val

                        action_params = {
                            "tool_to_call": "edit_file", "target_file": f_path, "action_type": action,
                            "start_line_number": start_line, "end_line_number": end_line, 
                            "replacement_content": replace_str, # Uses specific 'replacement_content_val'
                            "create_if_not_exists": create_if_not_exists,
                            "instructions": f"In file '{f_path}', replace lines {start_line}-{end_line} (1-indexed, inclusive) with 'replacement_content'. Agent to use context comments. If create_if_not_exists ({create_if_not_exists}) and file missing, this action might not be directly applicable unless it means creating a file with this content spanning these line numbers."
                        }
                    elif action == "delete_lines":
                        if start_line is None or end_line is None: raise ValueError(f"Action '{action}' requires 'start_line_number' and 'end_line_number'.")
                        if not os.path.exists(f_path) and not create_if_not_exists: raise FileNotFoundError(f"File '{f_path}' not found for {action}.")
                        action_params = {
                            "tool_to_call": "edit_file", "target_file": f_path, "action_type": action,
                            "start_line_number": start_line, "end_line_number": end_line, "create_if_not_exists": create_if_not_exists,
                            "instructions": f"In file '{f_path}', delete lines {start_line}-{end_line} (1-indexed, inclusive). Agent to use context comments. If create_if_not_exists ({create_if_not_exists}) and file missing, this action is likely not applicable."
                        }
                    else:
                        raise ValueError(f"Unsupported file action: {action}")

                    if action_params:
                        update_task_status_in_queue(task_id, "pending_agent_action", notes=f"Awaiting agent to execute: {action} on {f_path}", agent_action_details=action_params)
                        log_message(task_id, f"Task {task_id} now pending agent action for: {action} on {f_path}. Details: {action_params}")
                        log_message(task_id, f"Full agent_action_details for {action} on {f_path}: {json.dumps(action_params, indent=2)}", level="DEBUG")
                        return # Delegate this file operation and stop further processing in this cycle

                except (FileNotFoundError, ValueError) as e_file_val: # Catch local validation errors
                    log_message(task_id, f"Local validation failed for file op '{action}' on '{f_path}': {e_file_val}", level="ERROR")
                    write_task_error_file(task_id, type(e_file_val).__name__, str(e_file_val), traceback.format_exc(), True, f"Local check failed for file op {action} on {f_path}.")
                    all_file_ops_locally_validated = False
                    update_task_status_in_queue(task_id, "failed", notes=f"Local validation failed for file op '{action}' on '{f_path}'.")
                    archive_task_files(task_id, "failed")
                    return # Stop processing this task

            if not all_file_ops_locally_validated:
                log_message(task_id, "One or more file operations failed local validation. Task processing halted.", level="ERROR")
                update_task_status_in_queue(task_id, "failed", notes="File operation local validation failed.")
                archive_task_files(task_id, "failed")
                return

            log_message(task_id, "All specified file modifications have been delegated or processed.")
        else:
            log_message(task_id, "No file modifications specified in this task.")

        # --- Command Execution (Delegated to Agent) ---
        if commands_to_execute:
            log_message(task_id, "Preparing command executions for agent delegation...")
            # Delegate the entire list of commands_to_execute to the agent.
            # The agent will be responsible for running them sequentially.
            # Ensure task_specific_instruction_subdir is defined before this block, if not already
            task_specific_instruction_subdir = os.path.join(INSTRUCTIONS_DIR, task_id)


            agent_command_package = { # RENAME from action_params to avoid confusion with file_op's action_params
                "tool_to_call": "execute_commands_sequentially", # Agent will interpret this
                "commands_list": commands_to_execute, # List of command objects
                "rth_config": { # Agent will use these paths
                    "rth_script_path": RTH_SCRIPT_PATH_FOR_AGENT,
                    "python_executable_for_rth": PYTHON_EXE_FOR_RTH_FOR_AGENT,
                    # Agent should generate unique name in status_file_dir for each command's RTH status file
                    "status_file_dir_base_for_agent": task_specific_instruction_subdir, 
                    # Pass default timeouts which might be overridden by per-command settings
                    "timeout_total_default": 300, 
                    "timeout_launch_default": 60,
                    "timeout_activity_default": 120
                },
                "per_command_rth_outputs_base_dir": os.path.join(INSTRUCTIONS_DIR, task_id, "rth_outputs"),
                "instructions": (
                    "Execute the list of commands sequentially using robust_terminal_handler.py (RTH). "
                    "RTH script path and Python executable are provided in 'rth_config'. "
                    "For each command in 'commands_list': "
                    "1. Determine specific timeouts (use command's values or rth_config defaults). "
                    "2. Construct the RTH invocation string (ensure paths and arguments are correctly quoted/split). "
                    "3. Create a unique subdirectory (e.g., cmd_0, cmd_1) under 'per_command_rth_outputs_base_dir' for this command's RTH status, stdout, and stderr files. "
                    "4. Launch RTH, ensuring its --status-file-path points into this unique subdirectory. Also set --cwd if specified in the command item, and pass 'predefined_inputs' from command item if present. "
                    "5. Poll for the RTH status file. "
                    "6. On completion/timeout, read the RTH status, stdout, and stderr. "
                    "7. Log these outputs clearly. "
                    "8. Clean up the RTH status, stdout, and stderr files for that command. "
                    "If any command fails (RTH reports error, timeout, or non-zero subprocess exit code), stop executing further commands and report the overall operation as failed, including details of the failing command. "
                    "If all commands execute successfully, report overall success. "
                    "The agent is responsible for all aspects of RTH interaction."
                )
            }
            update_task_status_in_queue(task_id, "pending_agent_action", notes=f"Awaiting agent to execute {len(commands_to_execute)} command(s).", agent_action_details=agent_command_package)
            log_message(task_id, f"Task {task_id} now pending agent action for: {len(commands_to_execute)} command(s). First command: '{commands_to_execute[0]['command_string'] if commands_to_execute else 'N/A'}'.") # Removed verbose details here
            log_message(task_id, f"Full agent_command_package for commands: {json.dumps(agent_command_package, indent=2)}", level="DEBUG") # ADDED
            return # Task is now delegated to the agent

        else:
            log_message(task_id, "No commands to execute specified in this task.")

        # If we reach here, it means all operations were successfully delegated (or there were none).
        # The task is not yet "completed" from the bridge's perspective if it's pending agent action.
        # If it was truly just a placeholder task with no file/command ops, it can be marked completed.
        current_task_status = ""
        try:
            with open(TASK_QUEUE_FILE, "r", encoding="utf-8") as f_check:
                current_tasks_list = json.load(f_check)
                for t_check in current_tasks_list:
                    if t_check.get("task_id") == task_id:
                        current_task_status = t_check.get("status")
                        break
        except Exception as e_stat_check:
            log_message(task_id, f"Could not re-read task status to confirm completion: {e_stat_check}", "WARNING")


        if current_task_status not in ["pending_agent_action", "failed", "completed"]:
             update_task_status_in_queue(task_id, "completed", "Task processed by bridge (no further bridge actions required or all ops delegated).")
             archive_task_files(task_id, "processed")
        elif current_task_status == "failed": # Should have been handled and returned earlier
            log_message(task_id, "Task was marked failed during processing. No further action here.", "DEBUG")
            archive_task_files(task_id, "failed")
        elif current_task_status == "completed": # Already completed by agent or previous cycle
            log_message(task_id, "Task already marked completed. No further action here.", "DEBUG")
            archive_task_files(task_id, "processed")

    except FileNotFoundError as e_fnf: # Specific for pre-delegation checks
        tb_str = traceback.format_exc()
        log_message(task_id, f"Local pre-check FileNotFoundError during task processing: {e_fnf}\n{tb_str}", level="ERROR")
        write_task_error_file(task_id, "LocalFileNotFoundError", str(e_fnf), tb_str, True, "File specified for modification was not found locally before delegation. Ensure file exists or create_if_not_exists is used appropriately.")
        update_task_status_in_queue(task_id, "failed", f"Local FileNotFoundError: {e_fnf}")
        archive_task_files(task_id, "failed")
    except ValueError as e_val: # Specific for pre-delegation checks
        tb_str = traceback.format_exc()
        log_message(task_id, f"Local pre-check ValueError during task processing: {e_val}\n{tb_str}", level="ERROR")
        write_task_error_file(task_id, "LocalValueError", str(e_val), tb_str, True, "Invalid value or missing parameter for a file operation (e.g., missing line_marker). Check task instructions.")
        update_task_status_in_queue(task_id, "failed", f"Local ValueError: {e_val}")
        archive_task_files(task_id, "failed")        
    except Exception as e:
        tb_str = traceback.format_exc()
        log_message(task_id, f"Unhandled error processing task: {e}\n{tb_str}", level="CRITICAL")
        write_task_error_file(task_id, "UnhandledBridgeError", str(e), tb_str, True, "An unexpected error occurred in the cursor_bridge.py while processing the task.")
        update_task_status_in_queue(task_id, "failed", f"Unhandled bridge error: {e}")
        archive_task_files(task_id, "failed")

def get_next_task_from_queue():
    """Gets the oldest task with status 'pending_bridge_processing' from task_queue.json."""
    try:
        # Ensure the queue file exists, create if not (e.g., first run)
        if not os.path.exists(TASK_QUEUE_FILE):
            with open(TASK_QUEUE_FILE, "w", encoding="utf-8") as f:
                json.dump({"tasks": []}, f, indent=4)
            log_message(None, f"Task queue file {TASK_QUEUE_FILE} created.")
            return None

        with open(TASK_QUEUE_FILE, "r", encoding="utf-8") as f:
            tasks_data = json.load(f) # Changed variable name
            
            pending_tasks = [
                task for task in tasks_data.get("tasks", []) # MODIFIED THIS LINE
                if task.get("status") == "pending_bridge_processing"
            ]
            if not pending_tasks:
                return None

            # Sort by creation_timestamp (oldest first)
            pending_tasks.sort(key=lambda t: t.get("creation_timestamp", "")) 
            
            next_task = pending_tasks[0]
            log_message(next_task.get("task_id"), f"Found next task: {next_task.get('task_id')} - Objective: {next_task.get('objective')}")
            return next_task

    except FileNotFoundError:
        log_message(None, f"Task queue file {TASK_QUEUE_FILE} not found. Will be created on next check if tasks are added.", level="WARNING")
        return None
    except json.JSONDecodeError:
        log_message(None, f"Error decoding JSON from {TASK_QUEUE_FILE}. Check file integrity.", level="ERROR")
        # Consider renaming/backing up the corrupted file here
        corrupted_file_path = f"{TASK_QUEUE_FILE}.corrupted_{int(time.time())}"
        try:
            shutil.copy(TASK_QUEUE_FILE, corrupted_file_path)
            log_message(None, f"Backed up corrupted task queue to {corrupted_file_path}", level="WARNING")
            # Optionally, create a new empty queue
            with open(TASK_QUEUE_FILE, "w", encoding="utf-8") as f:
                json.dump({"tasks": []}, f, indent=4)
            log_message(None, f"Created new empty task queue after corruption.", level="WARNING")
        except Exception as copy_e:
            log_message(None, f"Failed to backup or recreate corrupted task queue: {copy_e}", level="CRITICAL")
        return None
    except Exception as e:
        log_message(None, f"Error getting next task: {e}\n{traceback.format_exc()}", level="ERROR")
        return None

def main_loop():
    log_message(None, "Cursor Bridge started. Polling for tasks...")
    ensure_dirs() # Ensure all necessary directories exist
    while True:
        try:
            task = get_next_task_from_queue()
            if task:
                log_message(task.get("task_id"), "Processing task...")
                process_task(task)
            else:
                # log_message(None, "No pending tasks. Waiting...") # Too verbose for frequent polling
                pass # Do nothing if no task
        except Exception as e:
            # This is a catch-all for unexpected errors in the main loop itself (outside process_task)
            tb_str = traceback.format_exc()
            log_message(None, f"CRITICAL ERROR IN MAIN LOOP: {e}\n{tb_str}", level="CRITICAL")
            # Potentially implement a mechanism to prevent rapid-fire logging if the error is persistent
            # For example, only log this critical error once every N minutes if it keeps happening.
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main_loop() 