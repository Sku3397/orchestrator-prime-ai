# C:\Users\Man\CursorAgentUtils\robust_terminal_handler.py
import argparse
import base64
import datetime
import json
import os
import platform
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from typing import List, Tuple, Optional, Dict, Union

# --- Global Configuration ---
RTH_LOG_LEVEL_ENV = os.environ.get("RTH_LOG_LEVEL", "INFO").upper()
DEFAULT_LAUNCH_TIMEOUT_SEC = 30
DEFAULT_ACTIVITY_TIMEOUT_SEC = 120
DEFAULT_TOTAL_TIMEOUT_SEC = 600

# --- RTH Internal Logging ---
def _rth_log(level: str, message: str):
    """RTH's internal logger, prints to its own stderr."""
    log_levels = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
    current_log_level_val = log_levels.get(RTH_LOG_LEVEL_ENV, 1) # Default to INFO
    message_level_val = log_levels.get(level.upper(), 1)

    if message_level_val >= current_log_level_val:
        timestamp = datetime.datetime.now().isoformat()
        print(f"RTH_LOG|{level.upper()}|{timestamp}| {message}", file=sys.stderr, flush=True)

# --- Core Command Execution ---
def execute_command(
    command_to_run: Union[str, List[str]],
    working_directory: Optional[str] = None,
    environment_variables: Optional[Dict[str, str]] = None,
    predefined_inputs: Optional[List[str]] = None,
    prompt_to_expect_for_stdin: Optional[str] = None, # Note: Less effective with temp file I/O
    launch_timeout: int = DEFAULT_LAUNCH_TIMEOUT_SEC,
    activity_timeout: int = DEFAULT_ACTIVITY_TIMEOUT_SEC,
    total_timeout: int = DEFAULT_TOTAL_TIMEOUT_SEC,
    kill_signal_on_timeout: str = "terminate",
    preserve_child_output_files: bool = False
) -> Tuple[Optional[int], str, str, str, Optional[str], Optional[str]]:
    """
    Executes a command, captures its output to temp files, handles timeouts.
    Returns: (exit_code, stdout_content, stderr_content, status_message, stdout_temp_path, stderr_temp_path)
    """
    _rth_log("DEBUG", f"execute_command called with command: {repr(command_to_run)}, preserve_files: {preserve_child_output_files}")
    
    final_status_message = "RTH_EXEC_PENDING"
    process_exit_code: Optional[int] = None
    captured_stdout_str = ""
    captured_stderr_str = ""
    
    child_stdout_temp_file_path: Optional[str] = None
    child_stderr_temp_file_path: Optional[str] = None
    
    process: Optional[subprocess.Popen] = None
    effective_env = os.environ.copy()
    if environment_variables:
        effective_env.update(environment_variables)

    try:
        # Determine shell usage: True if command_to_run is a string, False if it's a list
        use_shell = isinstance(command_to_run, str)
        _rth_log("DEBUG", f"Effective command for Popen: {repr(command_to_run)}, shell={use_shell}")

        # Create temp files for child's stdout and stderr
        # We get names, then Popen opens them with its own file objects
        temp_stdout_obj = tempfile.NamedTemporaryFile(mode='wb', delete=False, prefix="rth_child_stdout_")
        child_stdout_temp_file_path = temp_stdout_obj.name
        temp_stdout_obj.close() # Close our handle, Popen will manage its own

        temp_stderr_obj = tempfile.NamedTemporaryFile(mode='wb', delete=False, prefix="rth_child_stderr_")
        child_stderr_temp_file_path = temp_stderr_obj.name
        temp_stderr_obj.close() # Close our handle

        # <<< USER_REQUESTED_DEBUG_PRINT (1A) >>>
        print(f"RTH_EXEC_DEBUG: execute_command: child_stdout_temp_file_name='{child_stdout_temp_file_path}', child_stderr_temp_file_name='{child_stderr_temp_file_path}'", file=sys.stderr, flush=True)
        _rth_log("INFO", f"Child stdout temp file: {child_stdout_temp_file_path}")
        _rth_log("INFO", f"Child stderr temp file: {child_stderr_temp_file_path}")

        # Open file objects for Popen to write to
        # These must be kept open until Popen is done with them.
        stdout_popen_fh = open(child_stdout_temp_file_path, 'wb')
        stderr_popen_fh = open(child_stderr_temp_file_path, 'wb')

        process = subprocess.Popen(
            command_to_run,
            stdout=stdout_popen_fh,
            stderr=stderr_popen_fh,
            stdin=subprocess.PIPE,
            cwd=working_directory or os.getcwd(),
            env=effective_env,
            shell=use_shell
        )
        final_status_message = "RTH_EXEC_LAUNCHED"
        _rth_log("INFO", f"Process PID {process.pid} launched.")

        start_time = time.time()
        last_activity_time = start_time # For activity timeout based on process liveness

        input_thread = None
        if predefined_inputs and process.stdin:
            def _writer_thread(proc_stdin, inputs_list, stop_event):
                try:
                    for i, line_data in enumerate(inputs_list):
                        if stop_event.is_set():
                            _rth_log("DEBUG", "Input thread: Stop event set.")
                            break
                        _rth_log("DEBUG", f"Input thread: Sending input #{i+1}: {repr(line_data)}")
                        proc_stdin.write((line_data + '\n').encode('utf-8'))
                        proc_stdin.flush()
                        time.sleep(0.1) # Small delay
                    _rth_log("INFO", "Input thread: All inputs sent.")
                except Exception as e_write:
                    _rth_log("ERROR", f"Input thread: Error writing to stdin: {e_write}")
                finally:
                    if proc_stdin and not proc_stdin.closed:
                        _rth_log("DEBUG", "Input thread: Closing stdin.")
                        try: proc_stdin.close()
                        except Exception: pass # Ignore errors on close
            
            stop_input_event = threading.Event()
            input_thread = threading.Thread(target=_writer_thread, args=(process.stdin, predefined_inputs, stop_input_event))
            input_thread.daemon = True
            input_thread.start()
        elif process.stdin: # No inputs, close stdin immediately
            process.stdin.close()

        # Monitoring loop
        while True:
            current_time = time.time()
            if process.poll() is not None:
                process_exit_code = process.poll()
                final_status_message = "RTH_EXEC_COMPLETED_NORMALLY"
                _rth_log("INFO", f"Process PID {process.pid} exited with code {process_exit_code}.")
                break

            if current_time - start_time > total_timeout:
                final_status_message = "RTH_EXEC_TERMINATED_TOTAL_TIMEOUT"
                _rth_log("WARNING", f"Total timeout ({total_timeout}s) exceeded for PID {process.pid}.")
                break
            
            if final_status_message == "RTH_EXEC_LAUNCHED" and (current_time - start_time > launch_timeout):
                _rth_log("INFO", f"Process PID {process.pid} running past launch timeout ({launch_timeout}s).")
                final_status_message = "RTH_EXEC_RUNNING_PAST_LAUNCH_TIMEOUT" # Update status
            
            # Activity timeout: if process is running but total_timeout not hit
            if current_time - last_activity_time > activity_timeout:
                final_status_message = "RTH_EXEC_TERMINATED_ACTIVITY_TIMEOUT"
                _rth_log("WARNING", f"Activity timeout ({activity_timeout}s) for PID {process.pid} (process running, not completed).")
                break
            
            if process.poll() is None: # Still running
                last_activity_time = current_time

            time.sleep(0.1) # Polling interval

    except FileNotFoundError:
        final_status_message = "RTH_EXEC_ERROR_FILE_NOT_FOUND"
        _rth_log("ERROR", f"Command not found: {repr(command_to_run)}")
    except ValueError as ve: # e.g. from shlex.split if command_to_run was bad string
        final_status_message = f"RTH_EXEC_ERROR_INVALID_COMMAND_ARG: {ve}"
        _rth_log("ERROR", final_status_message)
    except Exception as e_exec:
        final_status_message = f"RTH_EXEC_ERROR_UNHANDLED_EXCEPTION: {type(e_exec).__name__}"
        _rth_log("ERROR", f"Unhandled exception during execute_command: {e_exec}\n{traceback.format_exc()}")
    finally:
        # Ensure Popen's file handles are closed before we try to read them
        if 'stdout_popen_fh' in locals() and stdout_popen_fh and not stdout_popen_fh.closed:
            stdout_popen_fh.close()
        if 'stderr_popen_fh' in locals() and stderr_popen_fh and not stderr_popen_fh.closed:
            stderr_popen_fh.close()

        if input_thread and input_thread.is_alive():
            _rth_log("DEBUG", "Signaling input thread to stop.")
            if 'stop_input_event' in locals(): stop_input_event.set()
            input_thread.join(timeout=1.0)
            if input_thread.is_alive(): _rth_log("WARNING", "Input thread did not stop gracefully.")

        if process and process.poll() is None: # If process still running (e.g., due to timeout break)
            _rth_log("WARNING", f"Process PID {process.pid} still running. Terminating with {kill_signal_on_timeout}...")
            try:
                if kill_signal_on_timeout == "kill": process.kill()
                else: process.terminate()
                process.wait(timeout=5) # Give time for termination
                _rth_log("INFO", f"Process PID {process.pid} {kill_signal_on_timeout}ed.")
            except Exception as e_term:
                _rth_log("ERROR", f"Error during {kill_signal_on_timeout} of PID {process.pid}: {e_term}. Attempting kill.")
                try: process.kill(); process.wait(timeout=2)
                except Exception as e_kill: _rth_log("ERROR", f"Final kill attempt failed for PID {process.pid}: {e_kill}")
        
        if process: process_exit_code = process.returncode # Get final exit code

        # Read from temp files
        if child_stdout_temp_file_path and os.path.exists(child_stdout_temp_file_path):
            try:
                with open(child_stdout_temp_file_path, 'r', encoding='utf-8', errors='replace') as f:
                    captured_stdout_str = f.read()
                _rth_log("DEBUG", f"Read {len(captured_stdout_str)} chars from stdout temp file: {child_stdout_temp_file_path}")
            except Exception as e_read:
                _rth_log("ERROR", f"Failed to read stdout temp file {child_stdout_temp_file_path}: {e_read}")
                captured_stderr_str += f"\n[RTH_IO_ERROR] Failed to read stdout temp: {e_read}"
        
        if child_stderr_temp_file_path and os.path.exists(child_stderr_temp_file_path):
            try:
                with open(child_stderr_temp_file_path, 'r', encoding='utf-8', errors='replace') as f:
                    captured_stderr_str = f.read() + captured_stderr_str # Prepend to any existing stderr
                _rth_log("DEBUG", f"Read {len(captured_stderr_str)} chars from stderr temp file: {child_stderr_temp_file_path}")
            except Exception as e_read:
                _rth_log("ERROR", f"Failed to read stderr temp file {child_stderr_temp_file_path}: {e_read}")
                # Avoid appending to stderr_str if it's the one failing
        
        # Cleanup temp files
        for p_path in [child_stdout_temp_file_path, child_stderr_temp_file_path]:
            if p_path and os.path.exists(p_path):
                if not preserve_child_output_files:
                    try: 
                        os.remove(p_path)
                        _rth_log("DEBUG", f"Removed temp file: {p_path}")
                    except Exception as e_del: 
                        _rth_log("WARNING", f"Failed to remove temp file {p_path}: {e_del}")
                else:
                    _rth_log("INFO", f"Preserving temp file as per --preserve-output-files: {p_path}")
        
        # Refine final status message based on exit code if process completed
        if final_status_message == "RTH_EXEC_COMPLETED_NORMALLY":
            final_status_message = "SUCCESS" if process_exit_code == 0 else f"FAILURE_EXIT_CODE_{process_exit_code}"
        
        # <<< USER_REQUESTED_DEBUG_PRINT (1B) >>>
        print(f"RTH_EXEC_DEBUG: execute_command: final_stdout_len={len(captured_stdout_str if captured_stdout_str is not None else '')}, final_stderr_len={len(captured_stderr_str if captured_stderr_str is not None else '')}", file=sys.stderr, flush=True)
        _rth_log("INFO", f"execute_command finished. Status: {final_status_message}, ExitCode: {process_exit_code}")

    return process_exit_code, captured_stdout_str, captured_stderr_str, final_status_message, child_stdout_temp_file_path, child_stderr_temp_file_path

# --- Self-Test Functions ---
def _run_rth_self_test(test_name: str, command: Union[str, List[str]], expected_status_prefix: str, expected_exit_code: Optional[int] = None, 
                       expected_stdout: Optional[str] = None, expected_stderr: Optional[str] = None, **kwargs):
    _rth_log("INFO", f"--- Running Self-Test: {test_name} ---")
    exit_code, stdout, stderr, status, _, _ = execute_command(command, **kwargs)
    
    passed = True
    if not status.startswith(expected_status_prefix):
        _rth_log("ERROR", f"  FAIL Status: Expected prefix '{expected_status_prefix}', Got '{status}'")
        passed = False
    if expected_exit_code is not None and exit_code != expected_exit_code:
        _rth_log("ERROR", f"  FAIL Exit Code: Expected {expected_exit_code}, Got {exit_code}")
        passed = False
    if expected_stdout is not None and stdout.strip() != expected_stdout.strip(): # Strip for comparison
        _rth_log("ERROR", f"  FAIL Stdout: Expected '{expected_stdout.strip()}', Got '{stdout.strip()}'")
        passed = False
    if expected_stderr is not None and stderr.strip() != expected_stderr.strip(): # Strip for comparison
        _rth_log("ERROR", f"  FAIL Stderr: Expected '{expected_stderr.strip()}', Got '{stderr.strip()}'")
        passed = False
    
    if passed: _rth_log("INFO", f"  PASS: {test_name}")
    else: _rth_log("ERROR", f"  FAILED: {test_name}")
    return passed

def run_all_rth_tests():
    _rth_log("INFO", "Starting all RTH self-tests...")
    py_exe = sys.executable
    results = []
    results.append(_run_rth_self_test("Simple Echo", [py_exe, "-c", "import sys; sys.stdout.write('RTH_TARGET_STDOUT'); sys.stderr.write('RTH_TARGET_STDERR'); sys.exit(0)"], "SUCCESS", 0, "RTH_TARGET_STDOUT", "RTH_TARGET_STDERR"))
    results.append(_run_rth_self_test("Exit Code 1", [py_exe, "-c", "sys.exit(1)"], "FAILURE_EXIT_CODE_1", 1))
    results.append(_run_rth_self_test("Total Timeout", [py_exe, "-c", "import time; time.sleep(5)"], "RTH_EXEC_TERMINATED_TOTAL_TIMEOUT", total_timeout=2))
    results.append(_run_rth_self_test("File Not Found", "non_existent_command_for_rth_test", "RTH_EXEC_ERROR_FILE_NOT_FOUND"))
    results.append(_run_rth_self_test("String Command (shell=True)", f"{py_exe} -c \"import sys; sys.stdout.write('shell_true_out')\"", "SUCCESS", 0, "shell_true_out"))

    if all(results): _rth_log("INFO", "All RTH self-tests PASSED.")
    else: _rth_log("ERROR", "One or more RTH self-tests FAILED.")
    return all(results)

# --- Main CLI Logic ---
if __name__ == "__main__":
    # <<< USER_REQUESTED_DEBUG_PRINT (EARLY) >>>
    import sys # Ensure sys is imported for this print
    print("RTH_SCRIPT_MAIN_STARTED_VERY_EARLY_DEBUG", file=sys.stderr, flush=True)

    main_execution_start_time = time.time()
    
    parser = argparse.ArgumentParser(description="Robust Terminal Handler for executing commands.")
    parser.add_argument("--target-command-base64", help="Base64 encoded target command and arguments (alternative to positional).")
    parser.add_argument("--cwd", help="Working directory for the command.")
    parser.add_argument("--env", help="JSON string of environment variables to set for the command.")
    parser.add_argument("--predefined-inputs", nargs="*", help="List of strings to be passed to the command's stdin sequentially.")
    parser.add_argument("--launch-timeout", type=int, default=DEFAULT_LAUNCH_TIMEOUT_SEC, help=f"Max seconds to wait for process to launch (default: {DEFAULT_LAUNCH_TIMEOUT_SEC}s).")
    parser.add_argument("--activity-timeout", type=int, default=DEFAULT_ACTIVITY_TIMEOUT_SEC, help=f"Timeout in seconds for process activity. If no new output or status change for this duration, process is terminated. Default: {DEFAULT_ACTIVITY_TIMEOUT_SEC}s")
    parser.add_argument("--total-timeout", type=int, default=DEFAULT_TOTAL_TIMEOUT_SEC, help=f"Total maximum runtime in seconds for the process. Default: {DEFAULT_TOTAL_TIMEOUT_SEC}s")
    parser.add_argument("--kill-signal", choices=['terminate', 'kill'], default='terminate', help="Signal to use on timeout (terminate or kill, default: terminate).")
    parser.add_argument("--status-file-path", required=True, help="Path to write the final JSON status file.")
    parser.add_argument("--run-tests", action="store_true", help="Run internal RTH self-tests.")
    parser.add_argument("--preserve-output-files", action="store_true", help="If set, the temporary stdout and stderr files created for the child process will not be deleted after RTH completes.")

    # Positional arguments for the command to run if --target-command-base64 is not used
    parser.add_argument('command_parts', nargs=argparse.REMAINDER, 
                        help="The command and its arguments to execute. Use '--' to separate RTH options from the target command if ambiguity arises.")

    args = parser.parse_args()

    # Initialize vars for status file and final exit
    rth_cli_final_exit_code = 1 
    rth_cli_status_message = "RTH_CLI_NOT_STARTED"
    rth_cli_subprocess_exit_code = None
    rth_cli_stdout_temp_path = None
    rth_cli_stderr_temp_path = None
    
    try:
        if args.run_tests:
            _rth_log("INFO", "[RTH_CLI] Option --run-tests specified.")
            all_tests_passed = run_all_rth_tests()
            rth_cli_status_message = "RTH_CLI_SELF_TESTS_COMPLETED"
            rth_cli_final_exit_code = 0 if all_tests_passed else 1
            # No status file for self-tests in this flow, output is via RTH_LOG
            sys.exit(rth_cli_final_exit_code)

        # Determine the command to execute
        command_to_process: Union[str, List[str], None] = None
        original_cmd_repr_for_log: str = ""

        if args.command_parts: # If positional arguments are present
            # The first part after '--' (if present) or the first unrecognized arg is the executable
            # All subsequent parts are arguments to that executable
            if '--' in args.command_parts:
                sep_index = args.command_parts.index('--')
                # Ensure there's something after '--' to be the command
                if len(args.command_parts) > sep_index + 1:
                    command_to_process = [args.command_parts[sep_index+1]] + args.command_parts[sep_index+2:]
                else:
                    parser.error("Command expected after '--' separator.")
            else: 
                # Assume the first element of command_parts is the executable if no '--'
                if args.command_parts:
                    command_to_process = [args.command_parts[0]] + args.command_parts[1:]
                else: # Should be caught by nargs=REMAINDER if no other command source
                    parser.error("No command specified in command_parts.")
            
            original_cmd_repr_for_log = repr(command_to_process)
            _rth_log("INFO", f"[RTH_CLI] Using command_parts (positional): {original_cmd_repr_for_log}")
        elif args.target_command_base64: # Check for target_command_base64 if command_parts is empty or not used
            try:
                command_to_process = base64.b64decode(args.target_command_base64.encode('utf-8')).decode('utf-8')
                original_cmd_repr_for_log = repr(command_to_process) # Log the decoded string
                _rth_log("INFO", f"[RTH_CLI] Using target_command_base64, decoded to: {original_cmd_repr_for_log}")
                # Specific fix for trailing backslash if it was an artifact of encoding
                if isinstance(command_to_process, str) and command_to_process.endswith('\\'):
                    _rth_log("DEBUG", f"[RTH_CLI] Stripping trailing backslash from decoded base64 command: {repr(command_to_process)}")
                    command_to_process = command_to_process[:-1]
            except Exception as e_b64_main:
                rth_cli_status_message = f"RTH_CLI_ERROR_BASE64_DECODE: {e_b64_main}"
                _rth_log("ERROR", f"[RTH_CLI] {rth_cli_status_message}")
                sys.exit(1) # Exit before status file write
        else: # This is the fallback if neither command_parts nor target_command_base64 is provided
            # This condition should ideally be caught by argparse configuration (e.g. making one of them required or mutually exclusive group)
            # For now, if not --run-tests, and no command_parts and no target_command_base64, then it's an error.
            if not args.run_tests: # Only error if not running tests, as tests don't need these.
                 parser.error("No command input: Neither positional command_parts nor --target-command-base64 provided, and not --run-tests.")


        if not command_to_process: # Should not be reached if parser.error works
            rth_cli_status_message = "RTH_CLI_ERROR_NO_COMMAND_INPUT"
            _rth_log("ERROR", f"[RTH_CLI] {rth_cli_status_message}")
            sys.exit(1)

        # Prepare inputs for execute_command
        final_command_list_for_exec: List[str]
        if isinstance(command_to_process, str):
            final_command_list_for_exec = command_to_process # execute_command will handle shell=True
        else: # list
            final_command_list_for_exec = command_to_process
        
        inputs_for_child_stdin = args.predefined_inputs
        
        # AutoAgent CLI user mode specific input handling
        is_aa_user_mode_target = False
        cmd_list_for_check = final_command_list_for_exec if isinstance(final_command_list_for_exec, list) else shlex.split(final_command_list_for_exec)
        if len(cmd_list_for_check) >= 3 and "autoagent.cli" in cmd_list_for_check[-3] and cmd_list_for_check[-2] == "main":
            main_idx = cmd_list_for_check.index("main")
            if main_idx == len(cmd_list_for_check) - 1 or cmd_list_for_check[main_idx+1].startswith("--"):
                is_aa_user_mode_target = True
        
        if is_aa_user_mode_target and args.predefined_inputs:
            _rth_log("INFO", "[RTH_CLI] AutoAgent user mode detected. Converting predefined_inputs to --test_input CLI args.")
            if not isinstance(final_command_list_for_exec, list): # Must be list to append
                final_command_list_for_exec = shlex.split(final_command_list_for_exec)
            for item in args.predefined_inputs:
                final_command_list_for_exec.append("--test_input")
                final_command_list_for_exec.append(item)
            inputs_for_child_stdin = None # Now passed as CLI args
            _rth_log("DEBUG", f"[RTH_CLI] Modified command for --test_input: {repr(final_command_list_for_exec)}")


        env_vars_dict = {}
        if args.env:
            for item in args.env:
                if '=' in item: key, val = item.split('=', 1); env_vars_dict[key] = val
        
        # Call execute_command
        rth_cli_subprocess_exit_code, stdout_str_main, stderr_str_main, rth_cli_status_message, \
        rth_cli_stdout_temp_path, rth_cli_stderr_temp_path = execute_command(
            command_to_run=final_command_list_for_exec, # Pass string or list
            working_directory=args.cwd,
            environment_variables=env_vars_dict,
            predefined_inputs=inputs_for_child_stdin,
            prompt_to_expect_for_stdin=None, # Revert to None, was args.prompt_to_expect_for_stdin
            launch_timeout=args.launch_timeout,
            activity_timeout=args.activity_timeout,
            total_timeout=args.total_timeout,
            kill_signal_on_timeout=args.kill_signal,
            preserve_child_output_files=args.preserve_output_files
        )

        # Determine final CLI exit code based on RTH execution outcome
        if rth_cli_status_message == "SUCCESS":
            rth_cli_final_exit_code = rth_cli_subprocess_exit_code if rth_cli_subprocess_exit_code is not None else 0
        else: # Any RTH error or timeout
            rth_cli_final_exit_code = 1 if rth_cli_subprocess_exit_code is None or rth_cli_subprocess_exit_code != 0 else 0


        # Print structured output for controlling agent
        print(f"ROBUST_HANDLER_CLI_ORIGINAL_COMMAND_FOR_LOG: {original_cmd_repr_for_log}")
        print(f"ROBUST_HANDLER_CLI_STATUS: {rth_cli_status_message}")
        print(f"ROBUST_HANDLER_CLI_EXIT_CODE: {rth_cli_subprocess_exit_code if rth_cli_subprocess_exit_code is not None else 'N/A'}")
        sys.stdout.write("ROBUST_HANDLER_CLI_STDOUT_START>>>")
        sys.stdout.write(stdout_str_main if stdout_str_main else "")
        sys.stdout.write("<<<ROBUST_HANDLER_CLI_STDOUT_END\n")
        sys.stdout.write("ROBUST_HANDLER_CLI_STDERR_START>>>")
        sys.stdout.write(stderr_str_main if stderr_str_main else "")
        sys.stdout.write("<<<ROBUST_HANDLER_CLI_STDERR_END\n")
        sys.stdout.flush()

    except SystemExit as e: # From parser.error or explicit sys.exit
        rth_cli_final_exit_code = e.code if isinstance(e.code, int) else 1
        if rth_cli_status_message == "RTH_CLI_NOT_STARTED": rth_cli_status_message = f"RTH_CLI_SYSTEM_EXIT_{rth_cli_final_exit_code}"
        _rth_log("ERROR", f"[RTH_CLI] SystemExit caught in main: {rth_cli_final_exit_code}")
    except Exception as e_cli:
        rth_cli_status_message = f"RTH_CLI_UNHANDLED_MAIN_EXCEPTION: {type(e_cli).__name__}"
        _rth_log("ERROR", f"[RTH_CLI] Unhandled main exception: {e_cli}\n{traceback.format_exc()}")
        rth_cli_final_exit_code = 3 # Distinct error code
    finally:
        main_execution_end_time = time.time()
        
        # <<< USER_REQUESTED_DEBUG_PRINT (2A) >>>
        print(f"RTH_MAIN_DEBUG: Preparing status_data. stdout_temp_file_name_main='{rth_cli_stdout_temp_path}', stderr_temp_file_name_main='{rth_cli_stderr_temp_path}', handler_status_message_main='{rth_cli_status_message}', subprocess_numeric_exit_code_main={rth_cli_subprocess_exit_code}", file=sys.stderr, flush=True)

        status_data_payload = {
            "final_handler_status_message": rth_cli_status_message,
            "subprocess_exit_code": rth_cli_subprocess_exit_code,
            "cli_script_exit_code": rth_cli_final_exit_code, # RTH script's own exit code
            "stdout_temp_file_used": rth_cli_stdout_temp_path, 
            "stderr_temp_file_used": rth_cli_stderr_temp_path, 
            "execution_start_time_epoch": main_execution_start_time,
            "execution_end_time_epoch": main_execution_end_time,
            "total_duration_seconds": round(main_execution_end_time - main_execution_start_time, 3)
        }
        # <<< USER_REQUESTED_DEBUG_PRINT (2B) >>>
        print(f"RTH_MAIN_DEBUG: status_data to be written to JSON: {repr(status_data_payload)}", file=sys.stderr, flush=True)

        if args and hasattr(args, 'status_file_path') and args.status_file_path:
            try:
                _rth_log("INFO", f"[RTH_CLI] Writing status file to: {args.status_file_path}")
                # Ensure directory for status file exists
                status_file_dir = os.path.dirname(args.status_file_path)
                if status_file_dir and not os.path.exists(status_file_dir):
                    os.makedirs(status_file_dir, exist_ok=True)
                with open(args.status_file_path, 'w', encoding='utf-8') as sf:
                    json.dump(status_data_payload, sf, indent=4)
                _rth_log("INFO", f"[RTH_CLI] Status file written successfully: {args.status_file_path}")
            except Exception as e_stat_write:
                _rth_log("ERROR", f"[RTH_CLI] Failed to write status file to {args.status_file_path}: {e_stat_write}")
                # Fallback: print JSON to RTH's stderr if file write fails
                print(f"RTH_CLI_STATUS_JSON_FALLBACK_START>>>\n{json.dumps(status_data_payload, indent=4)}\n<<<RTH_CLI_STATUS_JSON_FALLBACK_END", file=sys.stderr, flush=True)
        
        _rth_log("INFO", f"[RTH_CLI] Script final exit code: {rth_cli_final_exit_code}")
        sys.exit(rth_cli_final_exit_code)