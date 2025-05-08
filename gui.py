import customtkinter as ctk
import tkinter as tk
import os
from tkinter import simpledialog, messagebox, filedialog
from typing import Optional, List, Dict, Any
from persistence import load_projects, add_project, Project, get_project_by_name
from engine import OrchestrationEngine, EngineState

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
        # Re-entrancy guard
        if hasattr(self, '_currently_selecting_project') and self._currently_selecting_project == project_name:
            # print(f"DEBUG: on_project_selected re-entered for {project_name}, returning.")
            return
        self._currently_selecting_project = project_name
        # print(f"DEBUG: on_project_selected for {project_name}")

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
             self._currently_selecting_project = None # Release guard
             # Clear input field when project changes
             self.user_input_entry.delete(0, "end") 
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
                    self.add_message(msg.sender, msg.message, msg.timestamp)
                self.chat_display.configure(state="disabled")
            else:
                messagebox.showerror("Error", f"Failed to load project '{project_name}' or its state.", parent=self)
                self.project_goal_text.configure(state="normal")
                self.project_goal_text.delete("1.0", "end")
                self.project_goal_text.configure(state="disabled")
        self.update_button_states()
        self._currently_selecting_project = None # Release guard

    def start_task(self):
        print("DEBUG_GUI: start_task button clicked")
        if not self.engine.current_project:
            messagebox.showwarning("No Project", "Please select or add a project first.", parent=self)
            return
            
        # Check engine state before calling engine method
        allowed_states = [EngineState.IDLE, EngineState.PROJECT_SELECTED, EngineState.TASK_COMPLETE, EngineState.ERROR]
        if self.engine.state not in allowed_states:
             self.add_message("SYSTEM_STATUS", f"Engine is busy ({self.engine.state.name}). Cannot start a new task now.")
             print(f"GUI: Ignoring start_task command, engine state is {self.engine.state.name}")
             return

        instruction = self.user_input_entry.get().strip()
        if not instruction:
            # If no text, assume user wants to start based on goal only
            print("GUI: Starting task with no initial instruction (using project goal).")
        else:
            print(f"GUI: Starting task with initial instruction: {instruction}")
            self.user_input_entry.delete(0, "end") # Clear input after sending as initial instruction

        # Call engine's start_task (which now also has internal checks)
        self.engine.start_task(initial_user_instruction=instruction if instruction else None)
        # Engine state changes will trigger handle_engine_update -> update_button_states

    def send_user_input(self, event=None): # Bound to Send button and Enter key in input field
        print(f"DEBUG_GUI: send_user_input invoked, engine state: {self.engine.state.name}")
        
        # --- State Check ---
        busy_states = [EngineState.RUNNING_CALLING_GEMINI, EngineState.RUNNING_PROCESSING_LOG, EngineState.RUNNING_WAITING_LOG, EngineState.LOADING_PROJECT]
        if self.engine.state in busy_states:
            self.add_message("SYSTEM_STATUS", f"Engine is busy ({self.engine.state.name}). Please wait.")
            print(f"GUI: Ignoring user input, engine state is {self.engine.state.name}")
            return

        user_text = self.user_input_entry.get().strip()
        
        if self.engine.state == EngineState.PAUSED_WAITING_USER_INPUT:
            if not user_text:
                 messagebox.showwarning("Input Needed", "Please provide the requested input.", parent=self)
                 return
            print(f"GUI: Resuming task with user input: {user_text}")
            self.engine.resume_with_user_input(user_text)
            self.user_input_entry.delete(0, "end") # Clear after sending
        elif self.engine.state in [EngineState.IDLE, EngineState.PROJECT_SELECTED, EngineState.TASK_COMPLETE, EngineState.ERROR]:
             # Treat as starting a new task if engine is in a ready state
             if not self.engine.current_project:
                 messagebox.showwarning("No Project", "Please select or add a project first.", parent=self)
                 return
             if not user_text:
                  # Ask user if they want to start based on goal only? Or just require input?
                  # For now, let's require text to start via Send/Enter. Start button handles goal-only start.
                  messagebox.showwarning("Input Missing", "Please enter an initial instruction or use the 'Start Task' button.", parent=self)
                  return
                  
             print(f"GUI: Starting new task via Send/Enter with instruction: {user_text}")
             self.engine.start_task(initial_user_instruction=user_text)
             self.user_input_entry.delete(0, "end") # Clear after sending
        else:
             # Should not happen if busy_states check is correct, but as a fallback
             self.add_message("SYSTEM_STATUS", f"Cannot process input in current state: {self.engine.state.name}")
             print(f"GUI: Ignoring user input, unexpected engine state: {self.engine.state.name}")

    def pause_task(self):
        print("DEBUG_GUI: pause_task button clicked")
        # Add state check? Pause might be allowed in more states?
        self.engine.pause_task()

    def stop_task(self):
        print("DEBUG_GUI: stop_task button clicked")
        # Add state check? Stop might be allowed in most states?
        self.engine.stop_task()

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
        """Callback function for the engine to update the GUI."""
        # Ensure GUI updates happen on the main thread
        # print(f"DEBUG_GUI: Received handle_engine_update: Type={message_type}, Data={data}") # Can be noisy
        
        if message_type == "state_change":
            try:
                new_state = EngineState[data] # data should be the state name string
                self.update_button_states()
            except KeyError:
                 print(f"GUI Error: Received unknown engine state name '{data}'")
                 self.update_button_states(EngineState.ERROR) # Fallback state
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
            print(f"DEBUG_GUI: Received new_message: Sender={data.get('sender')}, Msg Len={len(data.get('message', ''))}") # Added debug
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

class NewProjectDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        
        self.title("Add New Project")
        self.geometry("400x350")
        
        self.result = None
        self._project_name_custom = ""
        self._workspace_path_custom = ""
        self._overall_goal_custom = ""

        self.grid_columnconfigure(0, weight=1)

        current_row = 0
        ctk.CTkLabel(self, text="Project Name:").grid(row=current_row, column=0, padx=20, pady=(10,0), sticky="w")
        current_row += 1
        self.name_entry = ctk.CTkEntry(self, width=360)
        self.name_entry.grid(row=current_row, column=0, padx=20, pady=(0,10), sticky="ew")
        
        current_row += 1
        ctk.CTkLabel(self, text="Workspace Root Path:").grid(row=current_row, column=0, padx=20, pady=(5,0), sticky="w")
        current_row += 1
        self.path_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.path_frame.grid(row=current_row, column=0, padx=20, pady=(0,10), sticky="ew")
        self.path_frame.grid_columnconfigure(0, weight=1)
        self.path_entry = ctk.CTkEntry(self.path_frame) 
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0,5))
        self.browse_button = ctk.CTkButton(self.path_frame, text="Browse...", width=80, command=self.browse_path)
        self.browse_button.pack(side="left", fill="none", expand=False)

        current_row += 1
        ctk.CTkLabel(self, text="Overall Goal:").grid(row=current_row, column=0, padx=20, pady=(5,0), sticky="w")
        current_row += 1
        self.goal_entry = ctk.CTkTextbox(self, height=100, activate_scrollbars=True)
        self.goal_entry.grid(row=current_row, column=0, padx=20, pady=(0,10), sticky="nsew")
        self.grid_rowconfigure(current_row, weight=1) 

        current_row += 1
        self.button_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.button_frame.grid(row=current_row, column=0, padx=20, pady=10, sticky="e")
        self.ok_button_custom = ctk.CTkButton(self.button_frame, text="OK", width=80, command=self._ok_event_custom)
        self.ok_button_custom.pack(side="right", padx=(5,0))
        self.cancel_button_custom = ctk.CTkButton(self.button_frame, text="Cancel", width=80, command=self._cancel_event_custom)
        self.cancel_button_custom.pack(side="right")
        
        # Make modal
        self.grab_set() 
        self.name_entry.focus()
        self.wait_window()

    def browse_path(self):
        path = filedialog.askdirectory(parent=self)
        if path:
            self.path_entry.delete(0, "end")
            self.path_entry.insert(0, path)

    def _ok_event_custom(self, event=None):
        # DEBUG: Print values before validation
        name_val = self.name_entry.get()
        path_val = self.path_entry.get()
        goal_val = self.goal_entry.get("1.0", "end-1c")
        print(f"DEBUG: _ok_event_custom: Name='{name_val}', Path='{path_val}', Goal='{goal_val}'")

        self._project_name_custom = name_val.strip()
        self._workspace_path_custom = path_val.strip()
        self._overall_goal_custom = goal_val.strip()

        print(f"DEBUG: _ok_event_custom (stripped): Name='{self._project_name_custom}', Path='{self._workspace_path_custom}', Goal='{self._overall_goal_custom}'")

        if not self._project_name_custom or not self._workspace_path_custom or not self._overall_goal_custom:
            print("DEBUG: Validation failed - Fields empty.")
            messagebox.showerror("Input Error", "All fields are required.", parent=self)
            return
        if not os.path.isdir(self._workspace_path_custom): # Use the custom var
            messagebox.showerror("Input Error", "Selected workspace path is not a valid directory.", parent=self)
            return
        
        self.result = (self._project_name_custom, self._workspace_path_custom, self._overall_goal_custom)
        
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
