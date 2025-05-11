import subprocess
import sys
import os
print(f"Attempting to run {os.path.abspath('main.py')} with python {sys.executable}")
process = subprocess.Popen(
    [sys.executable, "main.py"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding='utf-8',
    cwd="."
)
try:
    stdout, stderr = process.communicate(timeout=5)
    print(f"--- main.py STDOUT ---")
    print(stdout)
    print(f"--- main.py STDERR ---")
    print(stderr)
    print(f"--- main.py Exit Code: {process.returncode} ---")
except subprocess.TimeoutExpired:
    process.kill()
    stdout, stderr = process.communicate()
    print("--- main.py TIMEOUT ---")
    print(f"--- main.py STDOUT (on timeout) ---")
    print(stdout)
    print(f"--- main.py STDERR (on timeout) ---")
    print(stderr)
    print(f"--- main.py Exit Code (after kill): {process.returncode} ---") 