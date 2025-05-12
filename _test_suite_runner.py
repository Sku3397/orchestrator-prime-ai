import subprocess
import sys
import os
import argparse # Import argparse

print("DEBUG TEST_RUNNER: VERY VERY EARLY STARTING", file=sys.stderr)
sys.stderr.flush()

print("RUNNER_SCRIPT_STARTED", flush=True) # DEBUG: Runner started

TEST_SCRIPT_TO_RUN = "test_terminal_app.py"
STDOUT_FILE = "test_run_stdout.txt"
STDERR_FILE = "test_run_stderr.txt"

# --- Argument Parsing for the Runner ---
parser = argparse.ArgumentParser(description='Run Orchestrator Prime test suite script.')
parser.add_argument('--test', metavar='TC_ID', type=int,
                    help='Run only a specific test case by ID (e.g., 1, 20).')
parser.add_argument('--group', metavar='GROUP_NAME', type=str,
                    help='Run only tests belonging to a specific group.')
parser.add_argument('--failfast', action='store_true',
                    help='Stop execution after the first test failure.')

# Parse arguments passed to _test_suite_runner.py itself
runner_args, unknown_args = parser.parse_known_args() # Use parse_known_args to allow args for the test script

# Command to run the test script (test_terminal_app.py)
cmd_to_run = [sys.executable, TEST_SCRIPT_TO_RUN]

# Pass recognized runner arguments to the test script command
if runner_args.test is not None:
    cmd_to_run.extend(['--test', str(runner_args.test)])
if runner_args.group is not None:
    cmd_to_run.extend(['--group', runner_args.group])
if runner_args.failfast:
    cmd_to_run.append('--failfast')

# Note: any 'unknown_args' could also potentially be passed if needed, but for now, just passing the ones defined.
# cmd_to_run.extend(unknown_args) # Uncomment if you want to pass through all unknown args

print("RUNNER_SCRIPT_INFO: Running test_terminal_app.py directly.", flush=True)

# Add parent directory to Python path so test_terminal_app can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

try:
    import test_terminal_app
    print("RUNNER_SCRIPT_DEBUG: Successfully imported test_terminal_app.", flush=True)

    # Pass the parsed arguments directly to test_terminal_app.main()
    # test_terminal_app.main() will handle sys.exit() calls internally
    # We capture the exit code if it returns one, otherwise sys.exit handles it.
    print("RUNNER_SCRIPT_INFO: Calling test_terminal_app.main().", flush=True)
    # Store original sys.argv
    original_argv = sys.argv
    # Set sys.argv to simulate command line arguments for test_terminal_app
    sys.argv = [TEST_SCRIPT_TO_RUN] # First arg is script name
    
    # Add recognized arguments back into sys.argv format
    if runner_args.test is not None:
        sys.argv.extend(['--test', str(runner_args.test)])
    if runner_args.group is not None:
        sys.argv.extend(['--group', runner_args.group])
    if runner_args.failfast:
        sys.argv.append('--failfast')

    # Add any unknown arguments back as well if needed by the test script's parser
    sys.argv.extend(unknown_args)

    print(f"RUNNER_SCRIPT_DEBUG: Setting sys.argv for test_terminal_app.main() to: {sys.argv}", flush=True)
    
    # Call main function directly
    exit_code = test_terminal_app.main()

    # Restore original sys.argv
    sys.argv = original_argv

    # test_terminal_app.main() might call sys.exit() directly, which terminates the process.
    # If it returns a value, we use that as the exit code.
    if exit_code is not None:
        print(f"RUNNER_SCRIPT_INFO: test_terminal_app.main() returned exit code: {exit_code}", flush=True)
        sys.exit(exit_code)
    else:
        # If main returns None, assume success unless sys.exit was called internally
        print("RUNNER_SCRIPT_INFO: test_terminal_app.main() returned None, assuming success unless sys.exit was called.", flush=True)
        sys.exit(0)

except ImportError as e:
    print(f"RUNNER_SCRIPT_ERROR: Failed to import test_terminal_app: {e}", file=sys.stderr, flush=True)
    sys.exit(1) # Indicate failure due to import error
except Exception as e:
    print(f"RUNNER_SCRIPT_ERROR: Exception occurred while running test_terminal_app: {e}", file=sys.stderr, flush=True)
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(1) # Indicate failure due to exception 