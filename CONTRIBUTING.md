# Contributing to Orchestrator Prime

We welcome contributions to Orchestrator Prime! Whether you're fixing a bug, adding a feature, or improving documentation, your help is appreciated.

## How to Contribute

### Reporting Bugs
-   If you find a bug, please try to provide a detailed report. Include:
    -   Steps to reproduce the bug.
    -   Expected behavior.
    -   Actual behavior.
    -   Relevant logs (`orchestrator_prime.log`, `cursor_bridge.log`, RTH status files, task-specific logs from the `instructions/` directory).
    -   The content of `task_queue.json` if the bug relates to the Cursor Bridge Module.
    -   Your environment (OS, Python version).
-   Open an issue on the project's issue tracker (if available) or communicate this information to the development team.

### Suggesting Enhancements
-   For new features or enhancements, please provide:
    -   A clear description of the proposed enhancement.
    -   The motivation or use case for the enhancement.
    -   Any potential drawbacks or challenges.
-   Discuss your proposal with the team before investing significant time in implementation.

### Code Contributions
1.  **Fork & Branch (if using Git)**: If the project uses Git, fork the repository and create a new branch for your changes.
2.  **Coding Style**:
    -   Follow PEP 8 guidelines for Python code.
    -   Ensure code is well-commented, especially for complex logic.
    -   Add comprehensive docstrings to new modules, classes, and functions (see "Docstrings" section below).
3.  **Docstrings**:
    -   Use triple quotes (`"""Docstring goes here."""`).
    -   The first line should be a concise summary.
    -   Follow with a more detailed explanation if needed, separated by a blank line.
    -   For functions/methods, describe arguments, return values, and any exceptions raised. Consider using a recognized format like Google style or reStructuredText. Refer to `ORCHESTRATOR_PRIME_ARCHITECTURE.md` for conceptual examples for existing modules.
4.  **Testing**:
    -   If you add new functionality, please try to add corresponding tests.
    -   Ensure existing tests pass with your changes. Refer to `TESTING_GUIDE.md` (once created) for details on running tests.
5.  **Documentation**:
    -   If your changes affect user-facing behavior or the system architecture, please update `README.md`, `ORCHESTRATOR_PRIME_ARCHITECTURE.md`, or other relevant documentation files.
6.  **Pull Request (if using Git)**:
    -   Submit a pull request with a clear description of your changes.
    -   Reference any relevant issues.

## Key Areas for Contribution & Debugging

-   **`cursor_bridge.py` Stability**: This is currently the most critical area needing attention. Investigating and resolving the hangs/timeouts in this script is a high priority.
-   **Error Handling**: Improving error handling and reporting throughout the application.
-   **Testing**: Expanding test coverage for both the Orchestrator Prime Core and the Cursor Bridge Module.
-   **New Agent Actions**: Extending the Cursor Bridge Module to support new file operations or other agent capabilities.

## Getting Started

1.  Ensure you have Python 3.8+ installed.
2.  Set up a virtual environment:
    ```bash
    python -m venv venv
    # Windows
    .\venv\Scripts\activate
    # macOS/Linux
    source venv/bin/activate
    ```
3.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
4.  Familiarize yourself with the project structure:
    -   Read `README.md` for a general overview.
    -   Study `ORCHESTRATOR_PRIME_ARCHITECTURE.md` for a deeper understanding of the components and data flows.
    -   Review the Pydantic models in `models.py` as they define the core data structures for the Cursor Bridge Module.

Thank you for contributing! 