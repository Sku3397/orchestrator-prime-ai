import customtkinter as ctk
import tkinter as tk
from tkinter import simpledialog, messagebox, filedialog
from typing import Optional, List, Dict, Any
from .persistence import load_projects, add_project, Project, get_project_by_name
from .engine import OrchestrationEngine, EngineState # Assuming OrchestrationEngine is in engine.py

class App(ctk.CTk):
    def __init__(self, engine: OrchestrationEngine):
        super().__init__()

        self.engine = engine
        self.engine.gui_update_callback = self.handle_engine_update

        self.title("Orchestrator Prime")
        self.geometry("1000x700")

        ctk.set_appearance_mode("System")  # Modes: "System" (default), "Dark", "Light"
        ctk.set_default_color_theme("blue")  # Themes: "blue" (default), "green", "dark-blue"

        # --- Main Layout --- #
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- Top Frame: Project Selection & Management --- #
        self.top_frame = ctk.CTkFrame(self, height=50)
        self.top_frame.grid(row=0, column=0, columnspan=2, padx=10, pady=(10,5), sticky="ew")

        self.project_label = ctk.CTkLabel(self.top_frame, text="Project:")
        self.project_label.pack(side="left", padx=(10,5), pady=10)

        self.project_names = [p.name for p in load_projects()]
        self.project_dropdown_var = ctk.StringVar(value=self.project_names[0] if self.project_names else "")
        self.project_dropdown = ctk.CTkComboBox(self.top_frame, values=self.project_names, 
                                                variable=self.project_dropdown_var, command=self.on_project_selected)
        self.project_dropdown.pack(side="left", padx=5, pady=10)
        if not self.project_names:
            self.project_dropdown.set("No projects available")

        self.add_project_button = ctk.CTkButton(self.top_frame, text="Add New Project", command=self.add_new_project_dialog)
        self.add_project_button.pack(side="left", padx=5, pady=10)
        
        self.status_label_header = ctk.CTkLabel(self.top_frame, text="Status:")
        self.status_label_header.pack(side="left", padx=(20,5), pady=10)
        self.status_label_var = ctk.StringVar(value=f"Engine: {self.engine.state.name}")
        self.status_label = ctk.CTkLabel(self.top_frame, textvariable=self.status_label_var, width=150)
        self.status_label.pack(side="left", padx=5, pady=10)


        # --- Left Frame: Controls --- #
        self.controls_frame = ctk.CTkFrame(self, width=200)
        self.controls_frame.grid(row=1, column=0, padx=(10,5), pady=5, sticky="ns")

        self.start_button = ctk.CTkButton(self.controls_frame, text="Start Task", command=self.start_task)
        self.start_button.pack(pady=10, padx=10, fill="x")

        self.pause_button = ctk.CTkButton(self.controls_frame, text="Pause Task", command=self.engine.pause_task)
        self.pause_button.pack(pady=10, padx=10, fill="x")

        self.stop_button = ctk.CTkButton(self.controls_frame, text="Stop Task", command=self.engine.stop_task)
        self.stop_button.pack(pady=10, padx=10, fill="x")
        
        self.project_goal_label = ctk.CTkLabel(self.controls_frame, text="Project Goal:", anchor="w")
        self.project_goal_label.pack(pady=(20,0), padx=10, fill="x")
        self.project_goal_text = ctk.CTkTextbox(self.controls_frame, height=100, activate_scrollbars=True)
        self.project_goal_text.pack(pady=5, padx=10, fill="both", expand=True)
        self.project_goal_text.configure(state="disabled")

        # --- Main Right Frame: Chat Display and User Input --- #
        self.main_chat_frame = ctk.CTkFrame(self)
        self.main_chat_frame.grid(row=1, column=1, padx=(5,10), pady=5, sticky="nsew")
        self.main_chat_frame.grid_rowconfigure(0, weight=1)
        self.main_chat_frame.grid_columnconfigure(0, weight=1)

        self.chat_display = ctk.CTkTextbox(self.main_chat_frame, activate_scrollbars=True)
        self.chat_display.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        self.chat_display.configure(state="disabled")

        self.input_frame = ctk.CTkFrame(self.main_chat_frame, height=50)
        self.input_frame.grid(row=1, column=0, padx=5, pady=(5,10), sticky="ew")
        self.input_frame.grid_columnconfigure(0, weight=1)

        self.user_input_entry = ctk.CTkEntry(self.input_frame, placeholder_text="Enter initial instruction or response here...")
        self.user_input_entry.grid(row=0, column=0, padx=(5,5), pady=5, sticky="ew")
        self.user_input_entry.bind("<Return>", self.send_user_input)

        self.send_button = ctk.CTkButton(self.input_frame, text="Send", width=80, command=self.send_user_input)
        self.send_button.grid(row=0, column=1, padx=(0,5), pady=5)
        
        self.update_button_states()
        if self.project_dropdown_var.get() and self.project_dropdown_var.get() != "No projects available":
            self.on_project_selected(self.project_dropdown_var.get()) # Load initial project if one is selected
        else:
            self.handle_engine_update("state_change", self.engine.state.name) # Ensure buttons reflect IDLE state if no project

    def add_message(self, sender: str, message: str, timestamp: Optional[str] = None):
        self.chat_display.configure(state="normal")
        prefix = f"{sender.upper()}" 
        if timestamp:
            try:
                # Format timestamp nicely if possible
                from datetime import datetime
                ts_obj = datetime.fromisoformat(timestamp)
                formatted_ts = ts_obj.strftime("%Y-%m-%d %H:%M:%S")
                prefix = f"{formatted_ts} - {prefix}"
            except ValueError:
                prefix = f"{timestamp} - {prefix}"

        self.chat_display.insert("end", f"{prefix}:\n{message}\n\n")
        self.chat_display.see("end")
        self.chat_display.configure(state="disabled")

    def add_new_project_dialog(self):
        dialog = NewProjectDialog(self)
        if dialog.result:
            name, path, goal = dialog.result
            new_project = add_project(name, path, goal)
            if new_project:
                self.project_names.append(new_project.name)
                self.project_dropdown.configure(values=self.project_names)
                self.project_dropdown.set(new_project.name)
                self.on_project_selected(new_project.name)
                messagebox.showinfo("Success", f"Project '{new_project.name}' added.", parent=self)
            else:
                messagebox.showerror("Error", "Failed to add new project.", parent=self)

    def on_project_selected(self, project_name: str):
        if project_name == "No projects available" or not project_name:
             if self.engine.current_project:
                self.engine.stop_task() # Stop any task if project is deselected or invalid
             self.engine.current_project = None
             self.engine.current_project_state = None
             self.project_goal_text.configure(state="normal")
             self.project_goal_text.delete("1.0", "end")
             self.project_goal_text.configure(state="disabled")
             self.chat_display.configure(state="normal")
             self.chat_display.delete("1.0", "end")
             self.chat_display.configure(state="disabled")
             self.engine._set_state(EngineState.IDLE) # Use internal method for consistency
             self.update_button_states()
             return

        project = get_project_by_name(project_name)
        if project:
            success = self.engine.set_active_project(project)
            if success and self.engine.current_project and self.engine.current_project_state:
                self.project_goal_text.configure(state="normal")
                self.project_goal_text.delete("1.0", "end")
                self.project_goal_text.insert("1.0", self.engine.current_project.overall_goal)
                self.project_goal_text.configure(state="disabled")
                
                self.chat_display.configure(state="normal")
                self.chat_display.delete("1.0", "end")
                for msg in self.engine.current_project_state.conversation_history:
                    self.add_message(msg.get('sender', 'unknown'), msg.get('message', ''), msg.get('timestamp'))
                self.chat_display.configure(state="disabled")
            else:
                messagebox.showerror("Error", f"Failed to load project '{project_name}' or its state.", parent=self)
                self.project_goal_text.configure(state="normal")
                self.project_goal_text.delete("1.0", "end")
                self.project_goal_text.configure(state="disabled")
        self.update_button_states()

    def start_task(self):
        if not self.engine.current_project:
            messagebox.showwarning("No Project", "Please select or add a project first.", parent=self)
            return
        
        initial_input = self.user_input_entry.get().strip()
        # If engine is in a state where it needs user input for PAUSED_WAITING_USER_INPUT, this button might be 'Resume'
        if self.engine.state == EngineState.PAUSED_WAITING_USER_INPUT:
            if not initial_input:
                messagebox.showwarning("Input Needed", "Please provide your response in the input field.", parent=self)
                return
            self.engine.resume_with_user_input(initial_input)
        else:
            # Normal start, initial_input is optional for refining the goal or first step
            self.engine.start_task(initial_user_instruction=initial_input if initial_input else None)
        
        self.user_input_entry.delete(0, "end")

    def send_user_input(self, event=None): # Can be called by button or Enter key
        user_text = self.user_input_entry.get().strip()
        if not user_text:
            return

        if not self.engine.current_project:
            self.add_message("SYSTEM_INFO", "Please select a project to start.")
            # messagebox.showwarning("No Project", "Please select or add a project first.", parent=self)
            return

        current_engine_state = self.engine.state
        if current_engine_state == EngineState.PAUSED_WAITING_USER_INPUT:
            self.engine.resume_with_user_input(user_text)
        elif current_engine_state in [EngineState.IDLE, EngineState.PROJECT_SELECTED, EngineState.TASK_COMPLETE, EngineState.ERROR]:
            # Treat as starting the task with this input as the initial instruction/goal refinement
            self.engine.start_task(initial_user_instruction=user_text)
        else:
            # Undefined behavior for other states, perhaps log or show a message
            self.add_message("SYSTEM_INFO", f"Input ignored. Current task state: {current_engine_state.name}")
            # messagebox.showinfo("Info", f"Cannot process input in current state: {current_engine_state.name}", parent=self)
            return # Do not clear input if not used

        self.user_input_entry.delete(0, "end")

    def update_button_states(self):
        current_state = self.engine.state
        no_project = not self.engine.current_project

        if no_project:
            self.start_button.configure(text="Start Task", state="disabled")
            self.pause_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
            self.send_button.configure(state="disabled")
            self.user_input_entry.configure(state="disabled")
            return

        self.user_input_entry.configure(state="normal") # Generally enabled if project selected
        self.send_button.configure(state="normal")

        if current_state in [EngineState.IDLE, EngineState.PROJECT_SELECTED, EngineState.TASK_COMPLETE, EngineState.ERROR]:
            self.start_button.configure(text="Start Task", state="normal", command=self.start_task)
            self.pause_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
        elif current_state == EngineState.PAUSED_WAITING_USER_INPUT:
            self.start_button.configure(text="Resume with Input", state="normal", command=self.start_task) # Reuses start_task logic
            self.pause_button.configure(state="disabled") # Or could be an Abort/Stop here
            self.stop_button.configure(state="normal")
        elif current_state in [EngineState.RUNNING_WAITING_LOG, EngineState.RUNNING_CALLING_GEMINI, 
                               EngineState.RUNNING_PROCESSING_LOG, EngineState.RUNNING_WAITING_INITIAL_GEMINI]:
            self.start_button.configure(text="Start Task", state="disabled")
            self.pause_button.configure(state="normal")
            self.stop_button.configure(state="normal")
        else:
            # Default for unknown states, disable most things
            self.start_button.configure(text="Start Task", state="disabled")
            self.pause_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")

    def handle_engine_update(self, message_type: str, data: Any):
        # Ensure GUI updates run in the main thread
        # print(f"GUI received engine update: {message_type}, {data}") 
        if message_type == "state_change":
            self.status_label_var.set(f"Engine: {data}")
            if data == EngineState.PAUSED_WAITING_USER_INPUT.name:
                self.user_input_entry.focus()
                self.user_input_entry.configure(placeholder_text="Dev Manager needs your input...")
            elif data == EngineState.ERROR.name and self.engine.last_error_message:
                 self.status_label_var.set(f"Engine: ERROR") # Keep it short
                 self.add_message("SYSTEM_ERROR", self.engine.last_error_message)
            else:
                self.user_input_entry.configure(placeholder_text="Enter initial instruction or response here...")
            self.update_button_states()
        elif message_type == "error":
            self.add_message("ENGINE_ERROR", str(data))
            # No need to show modal for every engine error, status label and chat log should cover it.
            # messagebox.showerror("Engine Error", str(data), parent=self) 
        elif message_type == "project_loaded":
            self.status_label_var.set(f"Engine: {self.engine.state.name}") # Update from engine's actual state
            # project_name = data.get("project_name")
            # self.project_dropdown.set(project_name) # This might trigger on_project_selected again, be careful
            self.update_button_states()
        elif message_type == "new_message":
            self.add_message(data.get('sender', 'unknown'), data.get('message', ''), data.get('timestamp'))
        elif message_type == "user_input_needed":
            self.add_message("DEV_MANAGER", f"Input Needed: {data}")
            self.user_input_entry.focus()
            self.user_input_entry.configure(placeholder_text="Respond to Dev Manager...")
        elif message_type == "task_complete":
            self.add_message("DEV_MANAGER", f"Task Complete: {data}")
            messagebox.showinfo("Task Complete", "The Dev Manager has indicated the task is complete.", parent=self)
        elif message_type == "status_update":
            self.add_message("SYSTEM_STATUS", str(data))

    def on_closing(self):
        if messagebox.askokcancel("Quit", "Do you want to quit Orchestrator Prime?", parent=self):
            self.engine.shutdown() # Gracefully shutdown engine (e.g., stop file watcher)
            self.destroy()

class NewProjectDialog(ctk.CTkInputDialog):
    def __init__(self, parent):
        super().__init__(text="Enter project name:", title="Add New Project")
        self.geometry("400x300") # Adjust size if needed
        self.result = None
        self._project_name = ""
        self._workspace_path = ""
        self._overall_goal = ""

        # Override default input dialog structure
        self._title_label.pack_forget()
        self._entry.pack_forget()
        self._ok_button.pack_forget()
        self._cancel_button.pack_forget()

        ctk.CTkLabel(self._top_level, text="Project Name:").pack(pady=(10,0), padx=20, anchor="w")
        self.name_entry = ctk.CTkEntry(self._top_level, width=360)
        self.name_entry.pack(pady=(0,10), padx=20, fill="x")

        ctk.CTkLabel(self._top_level, text="Workspace Root Path:").pack(pady=(5,0), padx=20, anchor="w")
        self.path_frame = ctk.CTkFrame(self._top_level, fg_color="transparent")
        self.path_frame.pack(pady=(0,10), padx=20, fill="x")
        self.path_entry = ctk.CTkEntry(self.path_frame, width=300) # Adjust width
        self.path_entry.pack(side="left", fill="x", expand=True)
        self.browse_button = ctk.CTkButton(self.path_frame, text="Browse...", width=50, command=self.browse_path)
        self.browse_button.pack(side="left", padx=(5,0))

        ctk.CTkLabel(self._top_level, text="Overall Goal:").pack(pady=(5,0), padx=20, anchor="w")
        self.goal_entry = ctk.CTkTextbox(self._top_level, height=80, activate_scrollbars=True)
        self.goal_entry.pack(pady=(0,10), padx=20, fill="x", expand=True)

        self.ok_button_custom = ctk.CTkButton(self._top_level, text="OK", command=self._ok_event_custom)
        self.ok_button_custom.pack(side="right", padx=(5,20), pady=10)
        self.cancel_button_custom = ctk.CTkButton(self._top_level, text="Cancel", command=self._cancel_event_custom)
        self.cancel_button_custom.pack(side="right", padx=5, pady=10)
        
        self.name_entry.focus() # Set focus to the first entry
        self.grab_set() # Make modal
        self.wait_window() # Wait for window to close

    def browse_path(self):
        path = filedialog.askdirectory(parent=self._top_level)
        if path:
            self.path_entry.delete(0, "end")
            self.path_entry.insert(0, path)

    def _ok_event_custom(self, event=None):
        self._project_name = self.name_entry.get().strip()
        self._workspace_path = self.path_entry.get().strip()
        self._overall_goal = self.goal_entry.get("1.0", "end-1c").strip()

        if not self._project_name or not self._workspace_path or not self._overall_goal:
            messagebox.showerror("Input Error", "All fields are required.", parent=self._top_level)
            return
        if not os.path.isdir(self._workspace_path):
            messagebox.showerror("Input Error", "Selected workspace path is not a valid directory.", parent=self._top_level)
            return

        self.result = (self._project_name, self._workspace_path, self._overall_goal)
        self.grab_release()
        self.destroy()

    def _cancel_event_custom(self, event=None):
        self.result = None
        self.grab_release()
        self.destroy()

    def get_input(self):
        return self.result

if __name__ == '__main__':
    # This is for testing the GUI standalone, but it needs the engine.
    # In a real run, main.py would instantiate the engine and then the app.
    print("Please run from main.py to start the application with the OrchestrationEngine.")
    # Example of how it might be run (requires a dummy engine if run directly):
    class DummyEngine:
        def __init__(self):
            self.state = EngineState.IDLE
            self.current_project = None
            self.current_project_state = None
            self.gui_update_callback = None
            self.last_error_message = None
        def set_active_project(self,p): print(f"DummyEngine: set_active_project {p.name if p else 'None'}"); return True
        def start_task(self,i=None): print(f"DummyEngine: start_task, input: {i}")
        def pause_task(self): print("DummyEngine: pause_task")
        def stop_task(self): print("DummyEngine: stop_task")
        def resume_with_user_input(self,i): print(f"DummyEngine: resume_with_user_input {i}")
        def shutdown(self): print("DummyEngine: shutdown")
        def _set_state(self,s,e=None): self.state = s; print(f"DummyEngine: state set to {s}")

    # Create a dummy projects.json for testing if it doesn't exist
    if not os.path.exists("app_data"): os.makedirs("app_data")
    if not os.path.exists("app_data/projects.json"):
        with open("app_data/projects.json", "w") as f: json.dump([], f)
    
    # For the GUI to run, it expects config.ini and other files from the project structure
    # This basic run might fail if config.ini isn't found by ConfigManager used by OrchestrationEngine
    try:
        engine = OrchestrationEngine() # This will try to load config.ini
    except Exception as e:
        print(f"Could not initialize real engine for GUI test: {e}")
        print("Using DummyEngine for GUI test.")
        engine = DummyEngine()

    app = App(engine=engine)
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop() 