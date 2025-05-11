import subprocess
import sys
import os

print("RUNNER_SCRIPT_STARTED", flush=True) # DEBUG: Runner started

TEST_SCRIPT_TO_RUN = "test_terminal_app.py"
STDOUT_FILE = "test_run_stdout.txt"
STDERR_FILE = "test_run_stderr.txt"

# Command to run the test script (test_terminal_app.py)
cmd_to_run = [sys.executable, TEST_SCRIPT_TO_RUN]

print(f"RUNNER_SCRIPT_INFO: About to run: {' '.join(cmd_to_run)}", flush=True)
print(f"RUNNER_SCRIPT_INFO: STDOUT will be redirected to: {STDOUT_FILE}", flush=True)
print(f"RUNNER_SCRIPT_INFO: STDERR will be redirected to: {STDERR_FILE}", flush=True)

sub_exit_code = -1 # Default to an error code for the subprocess
all_tests_passed = True # Assume success initially

try:
    with open(STDOUT_FILE, 'w') as f_out, open(STDERR_FILE, 'w') as f_err:
        print(f"RUNNER_SCRIPT_INFO: Files for redirection opened.", flush=True)
        proc = subprocess.Popen(cmd_to_run, stdout=f_out, stderr=f_err, text=True, cwd=".", env=os.environ.copy())
        print(f"RUNNER_SCRIPT_INFO: Subprocess for {TEST_SCRIPT_TO_RUN} started (PID: {proc.pid}). Waiting...", flush=True)
        proc.wait() # Wait for the subprocess to complete
        sub_exit_code = proc.returncode
        print(f"RUNNER_SCRIPT_INFO: Subprocess {TEST_SCRIPT_TO_RUN} completed with exit code: {sub_exit_code}", flush=True)

        if sub_exit_code != 0:
            print(f"RUNNER_SCRIPT_FAILURE_DETECTED: Test script {TEST_SCRIPT_TO_RUN} failed with exit code {sub_exit_code}.", flush=True)
            all_tests_passed = False
        else:
            print(f"RUNNER_SCRIPT_SUCCESS_DETECTED: Test script {TEST_SCRIPT_TO_RUN} passed with exit code {sub_exit_code}.", flush=True)

except Exception as e:
    print(f"RUNNER_SCRIPT_ERROR: Exception occurred in runner: {e}", flush=True)
    all_tests_passed = False # Runner itself had an error, so tests didn't pass overall
    sub_exit_code = 99 # Special exit code to indicate runner script's own error
finally:
    # This print is useful for logging the outcome before exiting based on all_tests_passed.
    print(f"RUNNER_SCRIPT_FINAL_SUMMARY: Test script ({TEST_SCRIPT_TO_RUN}) exit code was {sub_exit_code}. Runner determined all_tests_passed: {all_tests_passed}", flush=True)

# Determine final exit code for _test_suite_runner.py itself
if not all_tests_passed:
    sys.exit(1)
else:
    sys.exit(0) 