import customtkinter as ctk
from gui import App # Assuming gui.py is in the same directory
from engine import OrchestrationEngine
import os

# Ensure the app_data directory exists, as persistence.py might expect it early
if not os.path.exists("app_data"):
    try:
        os.makedirs("app_data")
        print("Created app_data directory.")
        # Create an empty projects.json if it doesn't exist, as persistence.py might need it
        projects_json_path = os.path.join("app_data", "projects.json")
        if not os.path.exists(projects_json_path):
            with open(projects_json_path, 'w') as f:
                f.write("[]")
            print(f"Created empty {projects_json_path}.")

    except OSError as e:
        print(f"Error creating app_data directory or projects.json: {e}")
        # Depending on severity, you might want to exit or handle this more gracefully

if __name__ == "__main__":
    engine_instance = None
    try:
        # Initialize the engine first, as it might load configurations
        engine_instance = OrchestrationEngine()
        
        app = App(engine=engine_instance)
        app.protocol("WM_DELETE_WINDOW", app.on_closing) # Handle window close
        app.mainloop()

    except FileNotFoundError as e:
        # This typically happens if config.ini is missing and ConfigManager raises it.
        print(f"ERROR: Missing critical file: {e}")
        print("Please ensure 'config.ini' exists and is correctly configured.")
        # Optionally, show a simple Tkinter error dialog if GUI components can be loaded
        try:
            root = ctk.CTk()
            root.withdraw() # Hide the main window
            ctk.CTkMessageBox(title="Startup Error", message=f"Failed to start Orchestrator Prime.\nMissing critical file: {e}\nPlease ensure 'config.ini' is present and correctly configured.", icon="cancel")
            root.destroy()
        except Exception as me:
            print(f"Could not show GUI error message: {me}")

    except ValueError as e:
        # This typically happens if API key in config.ini is placeholder or missing.
        print(f"ERROR: Configuration error: {e}")
        print("Please ensure your Gemini API key is correctly set in 'config.ini'.")
        try:
            root = ctk.CTk()
            root.withdraw()
            ctk.CTkMessageBox(title="Startup Error", message=f"Failed to start Orchestrator Prime.\nConfiguration error: {e}\nPlease check your API key in 'config.ini'.", icon="cancel")
            root.destroy()
        except Exception as me:
            print(f"Could not show GUI error message: {me}")

    except ImportError as e:
        print(f"ERROR: Missing dependency: {e}")
        print("Please ensure all dependencies from requirements.txt are installed.")
        try:
            root = ctk.CTk()
            root.withdraw()
            ctk.CTkMessageBox(title="Startup Error", message=f"Failed to start Orchestrator Prime.\nMissing dependency: {e}\nPlease install dependencies from requirements.txt.", icon="cancel")
            root.destroy()
        except Exception as me:
            print(f"Could not show GUI error message: {me}")
            
    except Exception as e:
        print(f"An unexpected error occurred during startup: {e}")
        import traceback
        traceback.print_exc()
        try:
            root = ctk.CTk()
            root.withdraw()
            ctk.CTkMessageBox(title="Critical Error", message=f"An unexpected critical error occurred: {e}", icon="cancel")
            root.destroy()
        except Exception as me:
            print(f"Could not show GUI critical error message: {me}")

    finally:
        if engine_instance:
            engine_instance.shutdown() # Ensure engine cleans up, e.g., stops threads 