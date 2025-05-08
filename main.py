import customtkinter as ctk
from gui import App 
from engine import OrchestrationEngine
import os
import sys # For better error handling exit
import traceback # For detailed exception printing

# It's good practice for persistence layer to handle its own directory/file creation.
# The initial check in main.py can be a fallback or removed if persistence is robust.
# For now, let's keep it to ensure app_data exists for ConfigManager or other early needs
# if they don't trigger persistence.load_projects() first.

def ensure_app_data_scaffolding():
    """Ensures basic app_data directory and an empty projects.json exists."""
    app_data_dir = "app_data"
    projects_json_path = os.path.join(app_data_dir, "projects.json")
    try:
        if not os.path.exists(app_data_dir):
            os.makedirs(app_data_dir)
            print(f"Created directory: {app_data_dir}")
        
        # persistence.load_projects() will create projects.json if it doesn't exist,
        # so this explicit creation might be redundant if load_projects is called early.
        # However, having it here ensures it exists even if load_projects isn't the very first thing.
        if not os.path.exists(projects_json_path):
            with open(projects_json_path, 'w') as f:
                f.write("[]")
            print(f"Created empty projects file: {projects_json_path}")

    except OSError as e:
        print(f"CRITICAL ERROR: Could not create app_data directory or projects.json: {e}", file=sys.stderr)
        # Optionally, show a simple Tkinter error message if possible before exiting
        try:
            root_err = ctk.CTk()
            root_err.withdraw()
            ctk.CTkMessageBox(master=root_err, title="Startup Failure", message=f"Failed to create essential application data folder 'app_data'. Please check permissions.\nError: {e}")
            root_err.destroy()
        except Exception as tk_e:
            print(f"Could not show Tkinter error for app_data creation failure: {tk_e}", file=sys.stderr)
        sys.exit(1) # Exit if basic scaffolding fails

if __name__ == "__main__":
    ensure_app_data_scaffolding() # Ensure basic dirs/files are there

    engine_instance = None
    try:
        # Engine initialization might fail if config.ini is bad (e.g., API key missing)
        # GeminiCommunicator (called by Engine) handles this.
        engine_instance = OrchestrationEngine() # No longer takes callback in init
        
        app = App(engine=engine_instance) # Pass engine instance to GUI
        app.protocol("WM_DELETE_WINDOW", app.on_closing) 
        app.mainloop()

    except FileNotFoundError as e:
        print(f"ERROR (main): Missing critical file, likely config.ini. Details: {e}", file=sys.stderr)
        traceback.print_exc()
        try:
            root = ctk.CTk()
            root.withdraw()
            ctk.CTkMessageBox(master=root, title="Startup Error", message=f"Failed to start Orchestrator Prime.\nMissing critical file: {e}\nPlease ensure 'config.ini' is present and correctly configured.", icon="cancel")
            root.destroy()
        except Exception as me:
            print(f"Could not show GUI error message for FileNotFoundError: {me}", file=sys.stderr)
        sys.exit(1)

    except ValueError as e: # Often API key issues from GeminiComms via Engine
        print(f"ERROR (main): Configuration or Value error. Details: {e}", file=sys.stderr)
        traceback.print_exc()
        try:
            root = ctk.CTk()
            root.withdraw()
            ctk.CTkMessageBox(master=root, title="Startup Error", message=f"Failed to start Orchestrator Prime.\nConfiguration error: {e}\nPlease check your API key or other settings in 'config.ini'.", icon="cancel")
            root.destroy()
        except Exception as me:
            print(f"Could not show GUI error message for ValueError: {me}", file=sys.stderr)
        sys.exit(1)
            
    except ImportError as e:
        print(f"ERROR (main): Missing dependency. Details: {e}", file=sys.stderr)
        traceback.print_exc()
        missing_module_name = str(e).split("'")[-2] if "'" in str(e) else "a required library"
        try:
            root = ctk.CTk()
            root.withdraw()
            ctk.CTkMessageBox(master=root, title="Startup Error", message=f"Failed to start Orchestrator Prime.\nMissing dependency: {missing_module_name}\nPlease install all dependencies from requirements.txt.", icon="cancel")
            root.destroy()
        except Exception as me:
            print(f"Could not show GUI error message for ImportError: {me}", file=sys.stderr)
        sys.exit(1)
            
    except Exception as e: # Catch-all for any other unexpected startup errors
        print(f"CRITICAL UNEXPECTED ERROR (main) during startup: {e}", file=sys.stderr)
        traceback.print_exc()
        try:
            root = ctk.CTk() # Use ctk for consistency if available
            root.withdraw()
            ctk.CTkMessageBox(master=root, title="Critical Error", message=f"An unexpected critical error occurred during startup: {e}", icon="cancel")
            root.destroy()
        except Exception as me: # Fallback to basic tkinter if ctk fails
            print(f"Could not show GUI critical error message: {me}", file=sys.stderr)
            try:
                root_tk = ctk.CTk()
                root_tk.withdraw()
                ctk.CTkMessageBox(master=root_tk, title="Critical Error", message=f"An unexpected critical error occurred: {e}")
                root_tk.destroy()
            except Exception as tk_me:
                 print(f"Could not show basic Tkinter critical error message: {tk_me}", file=sys.stderr)
        sys.exit(1)

    finally:
        if engine_instance:
            print("Main: Initiating engine shutdown...")
            engine_instance.shutdown()
            print("Main: Application exited.") 
            engine_instance.shutdown()
            print("Main: Application exited.") 
            engine_instance.shutdown()
            print("Main: Application exited.") 