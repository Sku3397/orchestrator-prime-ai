# gui.py - (Assuming this version launches correctly for the user, despite agent's internal issues)
import customtkinter as ctk
import tkinter as tk
import os
import json # Added for dummy projects.json creation in __main__ block
from tkinter import simpledialog, messagebox, filedialog, Toplevel, Text, Scrollbar
from typing import Optional, List, Dict, Any
# Assuming persistence.py is correctly structured and provides these
# Note: If persistence.py ALSO uses relative imports, it will need fixing too.
# Assuming direct imports work from main.py context:
from persistence import load_projects, add_project, Project, get_project_by_name, PersistenceError
# Assuming engine.py is correctly structured and provides these
from engine import OrchestrationEngine, EngineState # Assuming EngineState is defined enum/class in engine
# Assuming models.py is correctly structured and provides these
from models import Turn # Assuming Turn is defined in models.py

import pyperclip # For SOP copy
from datetime import datetime # For formatting timestamps
import time # Added import

# Helper class for Settings Dialog (Incorporating Phase 3 elements)
class SettingsDialog(ctk.CTkDialog):
    def __init__(self, parent, engine: OrchestrationEngine):
        super().__init__(parent, title="Settings")
        self.engine = engine
        # Assuming engine provides access to its config_manager instance
        if not hasattr(self.engine, 'config'):
             messagebox.showerror("Internal Error", "Engine object missing 'config' attribute.", parent=parent)
             self.destroy()
             return
        self.config_manager = self.engine.config
        self.geometry("500x400")

        self._api_key_var = ctk.StringVar()
        self._max_history_var = ctk.StringVar()
        self._max_tokens_var = ctk.StringVar()
        self._sum_interval_var = ctk.StringVar()

        self._load_current_settings()

        # API Key
        ctk.CTkLabel(self, text="Gemini API Key:").grid(row=0, column=0, padx=10, pady=5, sticky="w")
        self.api_key_entry = ctk.CTkEntry(self, textvariable=self._api_key_var, width=350)
        self.api_key_entry.grid(row=0, column=1, padx=10, pady=5, sticky="ew")

        # Separator
        ctk.CTkFrame(self, height=2, fg_color="gray").grid(row=1, column=0, columnspan=2, padx=10, pady=10, sticky="ew")
        ctk.CTkLabel(self, text="Active Project Context Settings (if a project is loaded):").grid(row=2, column=0, columnspan=2, padx=10, pady=5, sticky="w")

        # Max History Turns
        ctk.CTkLabel(self, text="Max Recent History Turns:").grid(row=3, column=0, padx=10, pady=5, sticky="w")
        self.max_history_entry = ctk.CTkEntry(self, textvariable=self._max_history_var)
        self.max_history_entry.grid(row=3, column=1, padx=10, pady=5, sticky="ew")

        # Max Context Tokens
        ctk.CTkLabel(self, text="Max Context Tokens (for Gemini prompt):").grid(row=4, column=0, padx=10, pady=5, sticky="w")
        self.max_tokens_entry = ctk.CTkEntry(self, textvariable=self._max_tokens_var)
        self.max_tokens_entry.grid(row=4, column=1, padx=10, pady=5, sticky="ew")

        # Summarization Interval
        ctk.CTkLabel(self, text="Summarize History Every (N instructions):").grid(row=5, column=0, padx=10, pady=5, sticky="w")
        self.sum_interval_entry = ctk.CTkEntry(self, textvariable=self._sum_interval_var)
        self.sum_interval_entry.grid(row=5, column=1, padx=10, pady=5, sticky="ew")

        # Buttons
        self.button_frame = ctk.CTkFrame(self, fg_color="transparent") # Explicitly set fg_color
        self.button_frame.grid(row=6, column=0, columnspan=2, pady=20)
        self.save_button = ctk.CTkButton(self.button_frame, text="Save Settings", command=self._save_settings)
        self.save_button.pack(side="left", padx=10)
        self.cancel_button = ctk.CTkButton(self.button_frame, text="Cancel", command=self.destroy)
        self.cancel_button.pack(side="left", padx=10)

        if not self.engine.active_project:
            self.max_history_entry.configure(state="disabled")
            self.max_tokens_entry.configure(state="disabled")
            self.sum_interval_entry.configure(state="disabled")

    def _load_current_settings(self):
        # Use config_manager method safely
        self._api_key_var.set(self.config_manager.get_api_key() or "")

        # Get settings from engine if project is active
        if self.engine.active_project and self.engine.active_project_state:
            project_settings = {
                "max_history_turns": self.engine.active_project_state.max_history_turns,
                "max_context_tokens": self.engine.active_project_state.max_context_tokens,
                "summarization_interval": self.engine.active_project_state.summarization_interval,
            }
            self._max_history_var.set(str(project_settings.get("max_history_turns", 10))) # Provide default again
            self._max_tokens_var.set(str(project_settings.get("max_context_tokens", 30000)))
            self._sum_interval_var.set(str(project_settings.get("summarization_interval", 5)))
        else:
            self._max_history_var.set("N/A")
            self._max_tokens_var.set("N/A")
            self._sum_interval_var.set("N/A")

    def _save_settings(self):
        new_api_key = self._api_key_var.get().strip()
        api_key_changed = False
        if new_api_key and new_api_key != (self.config_manager.get_api_key() or ""):
            if not self.config_manager.set_api_key(new_api_key):
                messagebox.showerror("Error", "Failed to save API key to config.ini.", parent=self)
                return # Don't close dialog if API key save fails
            else:
                api_key_changed = True
                print("DEBUG_SETTINGS: API Key saved to config.ini")

        project_settings_changed = False
        if self.engine.active_project and self.engine.active_project_state:
            try:
                new_settings = {
                    "max_history_turns": int(self._max_history_var.get()),
                    "max_context_tokens": int(self._max_tokens_var.get()),
                    "summarization_interval": int(self._sum_interval_var.get())
                }
                # Check if values actually changed before calling engine update
                if (new_settings["max_history_turns"] != self.engine.active_project_state.max_history_turns or
                    new_settings["max_context_tokens"] != self.engine.active_project_state.max_context_tokens or
                    new_settings["summarization_interval"] != self.engine.active_project_state.summarization_interval):

                    if not self.engine.update_active_project_context_settings(new_settings):
                         # Error should have been shown by engine's callback
                         pass # Keep dialog open if engine reported error
                    else:
                        project_settings_changed = True
                        print("DEBUG_SETTINGS: Project context settings updated in engine state.")

            except ValueError:
                messagebox.showerror("Invalid Input", "Project context settings must be valid numbers.", parent=self)
                return # Don't close if project settings are invalid
            except Exception as e:
                 messagebox.showerror("Error", f"Failed to update project settings: {e}", parent=self)
                 return

        if api_key_changed:
            # Attempt to re-initialize communicator AFTER potentially showing settings saved message
            try:
                print("SettingsDialog: Re-initializing Gemini Communicator due to API key change...")
                # Assuming engine has a method to re-init or gemini_comms can be re-instantiated
                if hasattr(self.engine, 'reinitialize_gemini_communicator'):
                     self.engine.reinitialize_gemini_communicator()
                elif hasattr(self.engine, 'gemini_client'): # Fallback: Try direct re-instantiation
                     from gemini_comms import GeminiCommunicator # Local import ok here
                     self.engine.gemini_client = GeminiCommunicator()
                     print("SettingsDialog: Engine Gemini client re-initialized.")
                else:
                    print("SettingsDialog Warning: Could not find method to re-initialize Gemini client on engine.")
                    
                # Optionally show info, but maybe let the main app signal success/failure on next call
                # messagebox.showinfo("API Key", "API Key saved. Gemini client re-initialized.", parent=self)

            except Exception as e:
                messagebox.showerror("API Key Error", f"API Key saved, but failed to re-initialize Gemini client: {e}", parent=self)
                # Don't close dialog if re-init fails - user needs to fix API key or restart app
                return

        if api_key_changed or project_settings_changed:
             messagebox.showinfo("Settings Saved", "Settings have been saved successfully.", parent=self)

        self.destroy()


# Helper class for Text Viewers (Context, SOP) (From Phase 3)
class TextViewerDialog(ctk.CTkToplevel):
    def __init__(self, parent, title: str, text_content: str):
        super().__init__(parent)
        self.title(title)
        self.geometry("700x500")
        self.grid_columnconfigure(0, weight=1) # Allow resizing
        self.grid_rowconfigure(0, weight=1) # Allow resizing

        # Frame to hold textbox and scrollbar
        text_frame = ctk.CTkFrame(self)
        text_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)

        self.textbox = ctk.CTkTextbox(text_frame, wrap="word", activate_scrollbars=False) # Scrollbar handled by frame
        self.textbox.grid(row=0, column=0, sticky="nsew")

        # Scrollbar (example - might need adjustment based on CTkTextbox internal scrollbars)
        # self.scrollbar = ctk.CTkScrollbar(text_frame, command=self.textbox.yview)
        # self.scrollbar.grid(row=0, column=1, sticky="ns")
        # self.textbox.configure(yscrollcommand=self.scrollbar.set)

        self.textbox.insert("1.0", text_content)
        self.textbox.configure(state="disabled") # Make read-only

        # Use a frame for the button for better padding control
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=1, column=0, pady=(0, 10))
        self.close_button = ctk.CTkButton(button_frame, text="Close", command=self.destroy)
        self.close_button.pack()

        self.grab_set() # Make modal
        self.lift() # Bring to front
        self.focus()

class App(ctk.CTk):
    def __init__(self, engine: OrchestrationEngine):
        super().__init__()

        self.engine = engine
        # Register callbacks with the engine
        self.engine.gui_callback_update_status = self.update_status_label
        self.engine.gui_callback_add_message = self.add_message_to_chat
        self.engine.gui_callback_request_user_input = self.prompt_user_for_input
        self.engine.gui_callback_clear_chat = self.clear_chat_display
        self.engine.gui_callback_load_history = self.load_conversation_history
        self.engine.gui_callback_show_error = self.show_error_message_box # From Phase 3

        self.title("Orchestrator Prime")
        self.geometry("1100x750") # Slightly larger

        # --- Menu Bar ---
        self.menu_bar = tk.Menu(self)
        self.configure(menu=self.menu_bar)

        # File Menu
        self.file_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="File", menu=self.file_menu)
        self.file_menu.add_command(label="Settings...", command=self.open_settings_dialog)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self.on_closing)

        # Tools Menu
        self.tools_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Tools", menu=self.tools_menu)
        self.tools_menu.add_command(label="View Current Context Prompt", command=self.view_current_context, state="disabled") # Start disabled
        self.tools_menu.add_command(label="Copy Cursor SOP to Clipboard", command=self.copy_sop_to_clipboard)
        self.tools_menu.add_command(label="Clear `next_step.txt` for Active Project", command=self.clear_next_step_file, state="disabled") # Start disabled


        # --- Main Layout --- #
        self.grid_columnconfigure(1, weight=1)
        # Use row 1 for main content below menu/top bar
        self.grid_rowconfigure(1, weight=1)

        # --- Top Frame: Project Selection & Management --- #
        self.top_frame = ctk.CTkFrame(self, height=50)
        self.top_frame.grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="ew")

        self.project_label = ctk.CTkLabel(self.top_frame, text="Project:")
        self.project_label.pack(side="left", padx=(10, 5), pady=10)

        self.project_names = [""] # Start with empty option
        try:
            loaded_projects = load_projects()
            if loaded_projects:
                self.project_names.extend([p.name for p in loaded_projects])
            else: # Handle case where projects.json is empty or loading fails
                 self.project_names = ["No projects found"]
        except Exception as e:
            print(f"GUI Init Error: Failed to load projects: {e}")
            self.project_names = ["Error loading projects"]
            messagebox.showerror("Project Load Error", f"Failed to load projects from app_data/projects.json\n{e}")

        self.project_dropdown_var = ctk.StringVar(value=self.project_names[0])
        self.project_dropdown = ctk.CTkComboBox(self.top_frame, values=self.project_names,
                                                variable=self.project_dropdown_var, command=self.on_project_selected_ui)
        self.project_dropdown.pack(side="left", padx=5, pady=10)

        self.add_project_button = ctk.CTkButton(self.top_frame, text="Add New Project", command=self.add_new_project_dialog)
        self.add_project_button.pack(side="left", padx=5, pady=10)

        self.status_label_header = ctk.CTkLabel(self.top_frame, text="Status:")
        self.status_label_header.pack(side="left", padx=(20, 5), pady=10)
        # Ensure engine state is accessed safely if engine init failed
        initial_status = self.engine.state.name if hasattr(self.engine, 'state') else "ENGINE_INIT_ERROR"
        self.status_text_var = ctk.StringVar(value=f"Engine: {initial_status}")
        self.status_label = ctk.CTkLabel(self.top_frame, textvariable=self.status_text_var, width=350, anchor="w")
        self.status_label.pack(side="left", padx=5, pady=10, fill="x", expand=True)


        # --- Left Frame: Controls --- #
        self.controls_frame = ctk.CTkFrame(self, width=250) # Slightly wider
        self.controls_frame.grid(row=1, column=0, padx=(10, 5), pady=5, sticky="ns")

        # Renamed button to be more descriptive, command maps to appropriate engine state action
        self.start_resume_button = ctk.CTkButton(self.controls_frame, text="Start Task", command=self.handle_start_resume_button)
        self.start_resume_button.pack(pady=10, padx=10, fill="x")

        # Replaced Pause with Stop (more definitive user action)
        self.stop_task_button = ctk.CTkButton(self.controls_frame, text="Stop Current Task", command=self.handle_stop_task_button)
        self.stop_task_button.pack(pady=10, padx=10, fill="x")

        self.project_goal_label = ctk.CTkLabel(self.controls_frame, text="Project Goal:", anchor="w")
        self.project_goal_label.pack(pady=(10,0), padx=10, fill="x")
        self.project_goal_text = ctk.CTkTextbox(self.controls_frame, height=100, activate_scrollbars=True)
        self.project_goal_text.pack(pady=5, padx=10, fill="both", expand=True)
        self.project_goal_text.configure(state="disabled") # Read-only display

        # --- Main Right Frame: Chat Display and User Input --- #
        self.main_chat_frame = ctk.CTkFrame(self)
        self.main_chat_frame.grid(row=1, column=1, padx=(5,10), pady=5, sticky="nsew")
        self.main_chat_frame.grid_rowconfigure(0, weight=1)
        self.main_chat_frame.grid_columnconfigure(0, weight=1)

        self.chat_display = ctk.CTkTextbox(self.main_chat_frame, activate_scrollbars=True, wrap="word")
        self.chat_display.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        self.chat_display.configure(state="disabled")

        self.input_frame = ctk.CTkFrame(self.main_chat_frame, height=50)
        self.input_frame.grid(row=1, column=0, padx=5, pady=(5,10), sticky="ew")
        self.input_frame.grid_columnconfigure(0, weight=1)

        self.user_input_entry = ctk.CTkEntry(self.input_frame, placeholder_text="Enter initial goal refinement or response here...")
        self.user_input_entry.grid(row=0, column=0, padx=(5,5), pady=5, sticky="ew")
        self.user_input_entry.bind("<Return>", self.handle_send_button) # Bind Enter key

        self.send_button = ctk.CTkButton(self.input_frame, text="Send", width=80, command=self.handle_send_button)
        self.send_button.grid(row=0, column=1, padx=(0,5), pady=5)

        # Initial state update
        self.update_button_states(self.engine.state if hasattr(self.engine, 'state') else None) # Update based on initial engine state
        
        # Load initial project AFTER mainloop starts if needed, or rely on user selection
        # If a project was pre-selected and valid, load it
        initial_project_name = self.project_dropdown_var.get()
        if initial_project_name and initial_project_name not in ["", "No projects found", "Error loading projects"]:
            self.after(100, lambda: self.on_project_selected_ui(initial_project_name)) # Load after GUI is up

        # Check for critical engine init error after GUI is somewhat up
        if hasattr(self.engine, '_last_critical_error') and self.engine._last_critical_error:
             self.show_error_message_box("Critical Engine Error", f"Engine failed to initialize properly:\n{self.engine._last_critical_error}\nThe application might not function correctly.")


    # --- Engine Callback Implementations ---
    # Make sure these handle potential errors if engine/state is None during shutdown etc.
    def update_status_label(self, status_text: str):
        def _update():
            if not hasattr(self, 'engine') or not hasattr(self.engine, 'active_project_state') or self.engine.active_project_state is None:
                 current_engine_status_name = EngineState.IDLE.name # Default if state unavailable
            else:
                 current_engine_status_name = self.engine.active_project_state.current_status

            # Make status messages more descriptive
            display_text = status_text # Use message passed from engine if available
            if current_engine_status_name == EngineState.RUNNING_WAITING_LOG.name and self.engine.active_project:
                try:
                    log_dir = self.engine.config.get_default_dev_logs_dir()
                    log_file = "cursor_step_output.txt"
                    rel_log_path = os.path.join(".", log_dir, log_file)
                    display_text = f"Running - Waiting for Cursor: {rel_log_path}"
                except Exception:
                    display_text = "Running - Waiting for Cursor log..." # Fallback
            elif current_engine_status_name.startswith("ERROR_") and hasattr(self.engine, 'active_project_state') and self.engine.active_project_state:
                 # Use the engine's state name directly for errors unless specific message passed
                 display_text = status_text if status_text != current_engine_status_name else current_engine_status_name

            self.status_text_var.set(f"Engine: {display_text}")
            self.update_button_states(EngineState[current_engine_status_name] if current_engine_status_name in EngineState.__members__ else None)
        # Use after schedule the update in the main Tkinter thread
        self.after(0, _update)


    def add_message_to_chat(self, turn: Turn): # Expects Turn object
        def _add():
            # Check if chat_display still exists (might be destroyed during shutdown)
            if not hasattr(self, 'chat_display') or not self.chat_display.winfo_exists():
                return
            try:
                self.chat_display.configure(state="normal")
                sender_prefix = turn.sender.upper().replace("_", " ") # Make sender more readable
                timestamp_str = ""
                try:
                    ts_obj = datetime.fromisoformat(turn.timestamp)
                    local_ts = ts_obj.astimezone()
                    timestamp_str = local_ts.strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError):
                    timestamp_str = turn.timestamp # Fallback to raw timestamp if parsing fails

                prefix = f"{timestamp_str} - {sender_prefix}"

                self.chat_display.insert("end", f"{prefix}:\n{turn.message}\n\n")
                self.chat_display.see("end") # Scroll to end
                self.chat_display.configure(state="disabled")
            except tk.TclError as e:
                 print(f"GUI Error: TclError adding message to chat (widget destroyed?): {e}")
            except Exception as e:
                 print(f"GUI Error: Unexpected error adding message to chat: {e}")
                 # Try to recover state if possible
                 try:
                      self.chat_display.configure(state="disabled")
                 except: pass # Ignore errors during recovery

        # Use after schedule the update in the main Tkinter thread
        self.after(0, _add)


    def prompt_user_for_input(self, question: str): # Question already added to chat by engine
        def _prompt():
            # Check if input entry still exists
            if not hasattr(self, 'user_input_entry') or not self.user_input_entry.winfo_exists():
                return
            self.user_input_entry.delete(0, "end")
            self.user_input_entry.configure(placeholder_text="Respond to Dev Manager...")
            self.user_input_entry.focus() # Bring focus to input field
            # Button states are updated via the state change callback
        # Use after schedule the update in the main Tkinter thread
        self.after(0, _prompt)

    def clear_chat_display(self):
        def _clear():
            if not hasattr(self, 'chat_display') or not self.chat_display.winfo_exists():
                return
            try:
                self.chat_display.configure(state="normal")
                self.chat_display.delete("1.0", "end")
                self.chat_display.configure(state="disabled")
            except tk.TclError as e:
                 print(f"GUI Error: TclError clearing chat display: {e}")
            except Exception as e:
                 print(f"GUI Error: Unexpected error clearing chat display: {e}")
                 try:
                      self.chat_display.configure(state="disabled")
                 except: pass
        # Use after schedule the update in the main Tkinter thread
        self.after(0, _clear)

    def load_conversation_history(self, history: List[Turn]):
        def _load():
            if not hasattr(self, 'chat_display') or not self.chat_display.winfo_exists():
                return
            # Clear existing chat first
            self.clear_chat_display()
            # Then add messages
            for turn in history:
                # This calls the already thread-safe add_message_to_chat via self.after
                self.add_message_to_chat(turn)
        # Use after schedule the update in the main Tkinter thread
        self.after(0, _load)


    def show_error_message_box(self, title: str, message: str):
        # This callback might be triggered from a background thread in the engine
        # Schedule the messagebox to run in the main GUI thread
        def _show():
            messagebox.showerror(title, message, parent=self) # Specify parent
        # Use after schedule the update in the main Tkinter thread
        self.after(0, _show)


    # --- Menu/Button Action Handlers ---
    def open_settings_dialog(self):
        if hasattr(self.engine, 'config'):
            # Pass self (main window) as parent
            dialog = SettingsDialog(self, self.engine)
            # self.wait_window(dialog) # CTkDialog might handle modality itself
        else:
            self.show_error_message_box("Error", "Engine or configuration manager not initialized correctly.")


    def view_current_context(self):
        if not self.engine.active_project:
            messagebox.showinfo("No Project", "Please select an active project to view its context.", parent=self)
            return
        # Add try-except block in case engine method fails
        try:
            context_text = self.engine.get_current_context_for_display()
            if context_text:
                 # Pass self as parent
                TextViewerDialog(self, "Current Gemini Prompt Context (Approximate)", context_text)
            else:
                 messagebox.showinfo("Context", "Could not retrieve current context information from the engine.", parent=self)
        except Exception as e:
             self.show_error_message_box("Error", f"Failed to get context from engine: {e}")


    def copy_sop_to_clipboard(self):
        try:
            sop_text = self.engine.get_cursor_sop_prompt() # Assumes engine has this method
            if not sop_text:
                 messagebox.showwarning("Not Found", "Cursor SOP prompt text is not available in the engine.", parent=self)
                 return
            pyperclip.copy(sop_text)
            messagebox.showinfo("SOP Copied", "Cursor SOP has been copied to your clipboard.", parent=self)
        except AttributeError:
            self.show_error_message_box("Error", "Engine does not have the 'get_cursor_sop_prompt' method.")
        except pyperclip.PyperclipException as e:
            self.show_error_message_box("Clipboard Error", f"Could not copy to clipboard. Is pyperclip installed and configured?\nError: {e}")
        except Exception as e:
            self.show_error_message_box("Error", f"An unexpected error occurred while copying SOP: {e}")

    def clear_next_step_file(self):
        if not self.engine.active_project:
            messagebox.showwarning("No Project", "Please select an active project to clear its 'next_step.txt'.", parent=self)
            return
        # Confirm with user
        if messagebox.askyesno("Confirm Clear", f"Are you sure you want to clear the 'next_step.txt' instruction file for project '{self.engine.active_project.name}'?\nThis cannot be undone and might interrupt Cursor if it was about to read it.", parent=self):
            try:
                if self.engine.clear_dev_instruction_file(): # Assumes engine method returns True/False
                    messagebox.showinfo("File Cleared", "'next_step.txt' has been cleared.", parent=self)
                # If False, assume engine's callback showed error
            except AttributeError:
                self.show_error_message_box("Error", "Engine does not have the 'clear_dev_instruction_file' method.")
            except Exception as e:
                self.show_error_message_box("Error", f"An unexpected error occurred: {e}")

    def handle_stop_task_button(self):
        print("DEBUG_GUI: stop_task button clicked")
        if not self.engine.active_project:
            # Button should be disabled, but double-check
            # messagebox.showwarning("No Project", "No active project to stop.", parent=self)
            return
        # Check if task is actually running or paused, ok to stop in error state too
        stoppable_states = [
             EngineState.RUNNING_CALLING_GEMINI, EngineState.RUNNING_WAITING_LOG,
             EngineState.RUNNING_PROCESSING_LOG, EngineState.PAUSED_WAITING_USER_INPUT,
             EngineState.SUMMARIZING_CONTEXT, EngineState.ERROR # Allow stopping from error
        ]
        # Allow stopping from error states too
        can_stop = (hasattr(self.engine, 'state') and (self.engine.state in stoppable_states or self.engine.state.name.startswith("ERROR_")))

        if can_stop:
            if messagebox.askyesno("Confirm Stop", "Are you sure you want to stop the current task and reset the engine state to IDLE?", parent=self):
                try:
                     self.engine.stop_current_task_gracefully() # Engine should update status via callback
                except AttributeError:
                    self.show_error_message_box("Error", "Engine does not have the 'stop_current_task_gracefully' method.")
                except Exception as e:
                    self.show_error_message_box("Error", f"An error occurred while stopping the task: {e}")
        else:
             # Provide feedback if already idle/complete
             if hasattr(self.engine, 'state') and self.engine.state in [EngineState.IDLE, EngineState.TASK_COMPLETE, EngineState.PROJECT_SELECTED]:
                  messagebox.showinfo("Info", "No active task is running.", parent=self)
             else: # Should not happen given button states, but catchall
                  messagebox.showwarning("Info", f"Cannot stop task in current state: {self.engine.state.name if hasattr(self.engine, 'state') else 'Unknown'}", parent=self)


    def add_new_project_dialog(self):
        # Use the updated NewProjectDialog class
        dialog = NewProjectDialog(self)
        result = dialog.get_input() # Get result after dialog closes

        if result:
            name, path, goal = result
            try:
                new_project = add_project(name, path, goal) # Assumes add_project handles validation
                if new_project:
                    # Refresh project list
                    self.project_names = [""] + [p.name for p in load_projects()]
                    self.project_dropdown.configure(values=self.project_names)
                    self.project_dropdown.set(new_project.name) # Select the new project
                    self.on_project_selected_ui(new_project.name) # Trigger loading the new project
                    messagebox.showinfo("Success", f"Project '{new_project.name}' added.", parent=self)
                else:
                    # add_project might return None if validation within persistence fails
                    messagebox.showerror("Error", "Failed to add new project (e.g., name conflict, invalid path).", parent=self)
            except PersistenceError as e:
                 messagebox.showerror("Storage Error", f"Failed to save project list: {e}", parent=self)
            except Exception as e:
                # Log detailed error for debugging
                print(f"ERROR adding project: {e}")
                import traceback
                traceback.print_exc()
                self.show_error_message_box("Error", f"An unexpected error occurred while adding project: {e}")


    def on_project_selected_ui(self, project_name: str): # Renamed from on_project_selected
        # Basic check for placeholder text
        if project_name in ["", "No projects found", "Error loading projects"]:
            if hasattr(self.engine, 'active_project') and self.engine.active_project:
                # If switching away from a valid project, stop its task
                try:
                     self.engine.stop_current_task_gracefully()
                except Exception as e:
                     print(f"Error stopping task during project deselect: {e}")
            self.engine.active_project = None
            self.engine.active_project_state = None
            self._clear_project_specific_ui()
            self.update_button_states(EngineState.IDLE)
            return

        # Prevent re-entrancy if possible (though callbacks should be safer now)
        if hasattr(self, '_selecting_project') and self._selecting_project:
            return
        self._selecting_project = True

        try:
            project = get_project_by_name(project_name)
            if project:
                 # Ensure previous task is stopped before switching context
                 if hasattr(self.engine, 'active_project') and self.engine.active_project and self.engine.active_project.name != project_name:
                      try:
                          self.engine.stop_current_task_gracefully()
                          time.sleep(0.1) # Brief pause to allow state update
                      except Exception as e:
                          print(f"Error stopping previous task during project switch: {e}")

                 self.engine.set_active_project(project) # Engine now handles loading state & callbacks
                 
                 # Update UI elements that engine doesn't directly control via simple status callback
                 if self.engine.active_project:
                     self.project_goal_text.configure(state="normal")
                     self.project_goal_text.delete("1.0", "end")
                     self.project_goal_text.insert("1.0", self.engine.active_project.overall_goal)
                     self.project_goal_text.configure(state="disabled")
                 # Engine's set_active_project should trigger callbacks for chat history and status
                 # We still need to call update_button_states explicitly after setting project
                 self.update_button_states(self.engine.state if hasattr(self.engine, 'state') else EngineState.ERROR)

            else:
                self.show_error_message_box("Project Find Error", f"Could not find project data for '{project_name}'.")
                self._clear_project_specific_ui()
                self.update_button_states(EngineState.ERROR)
        except Exception as e:
             print(f"Error during project selection: {e}")
             import traceback
             traceback.print_exc()
             self.show_error_message_box("Error", f"An unexpected error occurred selecting project '{project_name}': {e}")
             self._clear_project_specific_ui()
             self.update_button_states(EngineState.ERROR)
        finally:
            self._selecting_project = False


    def _clear_project_specific_ui(self):
        """ Helper to clear goal and chat when no project is selected """
        if hasattr(self, 'project_goal_text'):
            self.project_goal_text.configure(state="normal")
            self.project_goal_text.delete("1.0", "end")
            self.project_goal_text.configure(state="disabled")
        if hasattr(self, 'chat_display'):
            self.clear_chat_display()


    def handle_start_resume_button(self):
        print("DEBUG_GUI: start_resume_button clicked")
        if not self.engine.active_project or not self.engine.active_project_state:
            messagebox.showwarning("No Project", "Please select or add a project first.", parent=self)
            return

        user_text = self.user_input_entry.get().strip()
        current_engine_status = self.engine.state # Use engine's direct state

        if current_engine_status == EngineState.PAUSED_WAITING_USER_INPUT:
            if not user_text:
                messagebox.showwarning("Input Needed", "Please provide your response in the input field.", parent=self)
                return
            print(f"GUI: Calling engine.resume_with_user_input: '{user_text}'")
            self.engine.resume_with_user_input(user_text)
            self.user_input_entry.delete(0, "end") # Clear input
        elif current_engine_status in [EngineState.IDLE, EngineState.PROJECT_SELECTED, EngineState.TASK_COMPLETE] or current_engine_status == EngineState.ERROR: # Check actual EngineState enum members
            print(f"GUI: Calling engine.start_task. Initial instruction: '{user_text if user_text else '[None - Use Goal]'}'")
            self.engine.start_task(initial_user_instruction=user_text if user_text else None)
            if user_text: # Clear only if text was used as initial instruction
                self.user_input_entry.delete(0, "end")
        else: # Button should be disabled, but handle defensively
            self.add_message("SYSTEM_STATUS", f"Engine is busy ({current_engine_status.name}). Cannot start/resume now.")
            print(f"GUI: Ignoring start/resume command, engine state is {current_engine_status.name}")

    def handle_send_button(self, event=None): # Add event=None for binding
        print("DEBUG_GUI: send_user_input invoked via Send/Enter")
        # Consolidate logic: Send button effectively does the same as Start/Resume button
        self.handle_start_resume_button()


    def handle_stop_task_button(self):
        print("DEBUG_GUI: stop_task button clicked")
        if not self.engine.active_project:
            return # Button should be disabled

        stoppable_states = [ # Use EngineState members
             EngineState.RUNNING_CALLING_GEMINI, EngineState.RUNNING_WAITING_LOG,
             EngineState.RUNNING_PROCESSING_LOG, EngineState.PAUSED_WAITING_USER_INPUT,
             EngineState.SUMMARIZING_CONTEXT, EngineState.ERROR # Allow stopping from error
        ]
        can_stop = hasattr(self.engine, 'state') and self.engine.state in stoppable_states

        if can_stop:
            if messagebox.askyesno("Confirm Stop", "Stop current task and reset to IDLE?", parent=self):
                try:
                     self.engine.stop_current_task_gracefully()
                except AttributeError:
                    self.show_error_message_box("Error", "Engine lacks 'stop_current_task_gracefully' method.")
                except Exception as e:
                    self.show_error_message_box("Error", f"Error stopping task: {e}")
        else:
             if hasattr(self.engine, 'state') and self.engine.state in [EngineState.IDLE, EngineState.TASK_COMPLETE, EngineState.PROJECT_SELECTED]:
                  messagebox.showinfo("Info", "No active task running.", parent=self)
             else:
                  messagebox.showwarning("Info", f"Cannot stop in state: {self.engine.state.name if hasattr(self.engine, 'state') else 'Unknown'}", parent=self)


    def update_button_states(self, current_engine_state: Optional[EngineState]):
        # Centralized logic for enabling/disabling controls based on engine state
        no_project = not self.engine.active_project

        if no_project:
            # Disable everything project-related
            self.start_resume_button.configure(text="Start Task", state="disabled")
            self.stop_task_button.configure(state="disabled")
            self.send_button.configure(state="disabled")
            self.user_input_entry.configure(state="disabled", placeholder_text="Select or add a project")
            if hasattr(self, 'tools_menu'):
                self.tools_menu.entryconfigure("View Current Context Prompt", state="disabled")
                self.tools_menu.entryconfigure("Clear `next_step.txt` for Active Project", state="disabled")
            return

        # Enable tools menu items if project is active
        if hasattr(self, 'tools_menu'):
            self.tools_menu.entryconfigure("View Current Context Prompt", state="normal")
            self.tools_menu.entryconfigure("Clear `next_step.txt` for Active Project", state="normal")

        self.user_input_entry.configure(state="normal")
        self.send_button.configure(state="normal")

        if current_engine_state in [EngineState.IDLE, EngineState.PROJECT_SELECTED, EngineState.TASK_COMPLETE] or current_engine_state == EngineState.ERROR:
            self.start_resume_button.configure(text="Start Task", state="normal")
            self.stop_task_button.configure(state="disabled")
            self.user_input_entry.configure(placeholder_text="Enter initial instruction or refinement...")
            self.user_input_entry.focus() # Focus input when ready
        elif current_engine_state == EngineState.PAUSED_WAITING_USER_INPUT:
            self.start_resume_button.configure(text="Resume with Input", state="normal")
            self.stop_task_button.configure(state="normal")
            self.user_input_entry.configure(placeholder_text="Respond to Dev Manager...")
            self.user_input_entry.focus() # Focus input when waiting
        elif current_engine_state in [
            EngineState.RUNNING_CALLING_GEMINI, EngineState.RUNNING_WAITING_LOG,
            EngineState.RUNNING_PROCESSING_LOG, EngineState.SUMMARIZING_CONTEXT, # Added summarizing
            EngineState.LOADING_PROJECT # Added loading project
        ]:
            self.start_resume_button.configure(text="Task Running...", state="disabled")
            self.stop_task_button.configure(state="normal")
            self.user_input_entry.configure(state="disabled", placeholder_text="Engine is busy...")
        else: # Default for unknown or other states
            self.start_resume_button.configure(text="Start Task", state="disabled")
            self.stop_task_button.configure(state="disabled")
            self.user_input_entry.configure(state="disabled", placeholder_text="Engine in unknown state...")


    def on_closing(self):
        print("GUI: Close button clicked.")
        if messagebox.askokcancel("Quit", "Exit Orchestrator Prime?", parent=self):
            print("GUI: Proceeding with shutdown...")
            try:
                if hasattr(self.engine, 'shutdown'):
                    self.engine.shutdown()
            except Exception as e:
                print(f"GUI Error during engine shutdown: {e}")
            self.destroy()


# --- NewProjectDialog Class (Updated to match Phase 3 standards) ---
class NewProjectDialog(ctk.CTkDialog):
    def __init__(self, parent, title="Add New Project"):
        super().__init__(master=parent, title=title)
        self.parent_app = parent
        self.result: Optional[tuple[str, str, str]] = None

        self.geometry("450x300") # Adjust size as needed
        self.grid_columnconfigure(1, weight=1) # Allow path entry to expand

        ctk.CTkLabel(self, text="Project Name:").grid(row=0, column=0, padx=10, pady=(10,0), sticky="w")
        self.entry_name = ctk.CTkEntry(self, width=350) # Adjusted width
        self.entry_name.grid(row=0, column=1, columnspan=2, padx=10, pady=(10,0), sticky="ew")

        ctk.CTkLabel(self, text="Workspace Root Path:").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        self.entry_path = ctk.CTkEntry(self, width=250)
        self.entry_path.grid(row=1, column=1, padx=(10,0), pady=5, sticky="ew")
        self.browse_button = ctk.CTkButton(self, text="Browse...", width=50, command=self.browse_path)
        self.browse_button.grid(row=1, column=2, padx=(2,10), pady=5, sticky="e")

        ctk.CTkLabel(self, text="Overall Goal:").grid(row=2, column=0, padx=10, pady=5, sticky="nw")
        self.entry_goal = ctk.CTkTextbox(self, height=80, width=350) # Use width
        self.entry_goal.grid(row=2, column=1, columnspan=2, padx=10, pady=5, sticky="ew")

        self.button_frame = ctk.CTkFrame(self, fg_color="transparent") # Use frame for buttons
        self.button_frame.grid(row=3, column=0, columnspan=3, pady=20)

        self.ok_button = ctk.CTkButton(self.button_frame, text="OK", command=self._ok_event)
        self.ok_button.pack(side="left", padx=10)
        self.cancel_button = ctk.CTkButton(self.button_frame, text="Cancel", command=self._cancel_event)
        self.cancel_button.pack(side="left", padx=10)

        self.entry_name.focus() # Focus on first field
        self.grab_set() # Make modal
        # Remove wait_window() as CTkDialog handles modality differently

    def browse_path(self):
        # Ensure parent=self for proper modal behavior if OS dialogs support it
        path = filedialog.askdirectory(title="Select Project Workspace Root", parent=self)
        if path:
            # Ensure path is absolute and normalized for consistency
            abs_path = os.path.abspath(os.path.normpath(path))
            self.entry_path.delete(0, "end")
            self.entry_path.insert(0, abs_path)

    def _ok_event(self, event=None):
        name = self.entry_name.get().strip()
        path = self.entry_path.get().strip()
        goal = self.goal_entry.get("1.0", "end-1c").strip() # Correct way to get all text

        print(f"DEBUG NewProjectDialog OK: Name='{name}', Path='{path}', Goal='{goal}'")

        if not name or not path or not goal:
            messagebox.showerror("Missing Information", "All fields are required.", parent=self)
            return

        # Validate path is absolute and exists
        if not os.path.isabs(path):
            messagebox.showerror("Invalid Path", "Workspace Root Path must be an absolute path.", parent=self)
            return
        if not os.path.isdir(path):
            messagebox.showerror("Invalid Path", f"Selected path is not a valid directory:\n{path}", parent=self)
            return

        self.result = (name, path, goal)
        self.destroy() # Close dialog

    def _cancel_event(self, event=None):
        self.result = None
        self.destroy() # Close dialog

    def get_input(self): # Changed name for clarity
        # This method is typically called AFTER the dialog is closed (e.g., by wait_window in Tkinter, but CTkDialog might not need it)
        # The caller (`add_new_project_dialog`) now accesses `dialog.result` directly after dialog creation/waiting if needed.
        # Let's keep it for potential future use, though the caller logic was updated.
        return self.result

# --- Standalone Execution Guard ---
if __name__ == '__main__':
    # This block is for testing the GUI layout standalone.
    # It requires dummy classes and may not fully represent application behavior.
    print("Running gui.py directly for basic layout testing.")
    print("Functionality requires running from main.py with a real OrchestrationEngine.")

    # Example Dummy Engine State Enum (must match definition in engine.py)
    from enum import Enum, auto
    class EngineState(Enum):
        IDLE = auto()
        LOADING_PROJECT = auto()
        PROJECT_SELECTED = auto()
        RUNNING_CALLING_GEMINI = auto()
        RUNNING_WAITING_LOG = auto()
        RUNNING_PROCESSING_LOG = auto()
        PAUSED_WAITING_USER_INPUT = auto()
        SUMMARIZING_CONTEXT = auto()
        TASK_COMPLETE = auto()
        ERROR = auto()
        # Add other error states if defined
        ERROR_API_AUTH = auto()
        ERROR_GEMINI_CALL = auto()
        ERROR_GEMINI_TIMEOUT = auto()
        ERROR_FILE_WRITE = auto()
        ERROR_FILE_READ_LOG = auto()
        ERROR_PERSISTENCE = auto()
        ERROR_WATCHER = auto()
        ERROR_CURSOR_TIMEOUT = auto()


    # Example DummyEngine for standalone testing
    class DummyEngine:
        def __init__(self):
            self.state = EngineState.IDLE
            self.current_project = None
            self.current_project_state = None
            # Callbacks that the real engine would expect the GUI to set
            self.gui_callback_update_status = None
            self.gui_callback_add_message = None
            self.gui_callback_request_user_input = None
            self.gui_callback_clear_chat = None
            self.gui_callback_load_history = None
            self.gui_callback_show_error = None
            self._last_critical_error = None # Match attribute name
            # Dummy config for settings dialog
            self.config = type('obj', (object,), {'get_api_key': lambda: 'DUMMY_KEY_FOR_TEST', 'set_api_key': lambda k: print(f"DummyConfig: Set API Key {k}")})()

        def set_active_project(self,p):
            print(f"DummyEngine: set_active_project {p.name if p else 'None'}")
            if p:
                self.current_project = p
                # Simulate loading/creating state
                self.current_project_state = type('obj', (object,), {
                    'current_status': EngineState.PROJECT_SELECTED.name,
                    'conversation_history': [],
                    'context_summary': '',
                    'last_instruction_to_cursor': None,
                    'max_history_turns': 10,
                    'max_context_tokens': 30000,
                    'summarization_interval': 5,
                    'gemini_turns_since_last_summary': 0
                })()
                self._set_state(EngineState.PROJECT_SELECTED)
                # Simulate engine calling back to GUI
                if self.gui_callback_clear_chat: self.gui_callback_clear_chat()
                if self.gui_callback_load_history: self.gui_callback_load_history([])
                if self.gui_callback_update_status: self.gui_callback_update_status(EngineState.PROJECT_SELECTED.name)
                return True
            else:
                self.current_project = None
                self.current_project_state = None
                self._set_state(EngineState.IDLE)
                return False

        def start_task(self,initial_user_instruction=None):
            print(f"DummyEngine: start_task, input: {initial_user_instruction}")
            self._set_state(EngineState.RUNNING_WAITING_LOG)
            if self.gui_callback_add_message:
                 self.gui_callback_add_message(Turn(sender="DUMMY_GEMINI", message="Dummy instruction sent."))

        def pause_task(self): print("DummyEngine: pause_task")
        def stop_task(self):
            print("DummyEngine: stop_task")
            self._set_state(EngineState.IDLE)
        def resume_with_user_input(self,i): print(f"DummyEngine: resume_with_user_input {i}")
        def shutdown(self): print("DummyEngine: shutdown")
        def _set_state(self,s): # Simplified state setter for dummy
             self.state = s; print(f"DummyEngine: state set to {s.name}")
             if self.gui_callback_update_status: self.gui_callback_update_status(s.name)

        # Dummy methods for features called by GUI/Dialogs
        def get_active_project_context_settings(self):
             return {"max_history_turns": 10, "max_context_tokens": 30000, "summarization_interval": 5} if self.current_project_state else None
        def update_active_project_context_settings(self, settings): print(f"DummyEngine: Update settings {settings}"); return True
        def get_current_context_for_display(self): return "Dummy Context: Goal + Summary + History"
        def get_cursor_sop_prompt(self): return "Dummy SOP Prompt Text..."
        def clear_dev_instruction_file(self): print("DummyEngine: Clear next_step.txt"); return True
        def stop_current_task_gracefully(self):
            print("DummyEngine: stop_current_task_gracefully")
            self._set_state(EngineState.IDLE)

    # --- Dummy persistence functions for standalone testing ---
    # These are simplified and don't handle errors robustly like real persistence.py should
    # They also don't use the Project/ProjectState models properly
    def load_projects():
        print("Dummy load_projects called")
        if not os.path.exists("app_data"): os.makedirs("app_data")
        if not os.path.exists("app_data/projects.json"):
            with open("app_data/projects.json", "w") as f: json.dump([], f)
            return []
        try:
            with open("app_data/projects.json", "r") as f:
                # Need to convert dicts back to Project objects if persistence saves dicts
                # For dummy, just return basic objects
                projects_data = json.load(f)
                return [Project(name=p['name'], workspace_root_path=p['workspace_root_path'], overall_goal=p['overall_goal']) for p in projects_data]
        except (json.JSONDecodeError, FileNotFoundError, KeyError):
             print("Error reading dummy projects.json")
             return [] # Return empty on error for dummy

    def add_project(name, path, goal):
        print(f"Dummy add_project called: {name}")
        if not os.path.isabs(path) or not os.path.isdir(path): return None # Basic validation
        projects = load_projects()
        # Check for name conflict
        if any(p.name == name for p in projects): return None
        new_proj = Project(name=name, workspace_root_path=path, overall_goal=goal)
        # Convert to list of dicts for saving
        projects_data = [{"name": p.name, "workspace_root_path": p.workspace_root_path, "overall_goal": p.overall_goal} for p in projects]
        projects_data.append({"name": new_proj.name, "workspace_root_path": new_proj.workspace_root_path, "overall_goal": new_proj.overall_goal})
        try:
            with open("app_data/projects.json", "w") as f:
                json.dump(projects_data, f, indent=2)
            return new_proj
        except IOError:
            return None

    def get_project_by_name(name):
        print(f"Dummy get_project_by_name called: {name}")
        projects = load_projects()
        for p in projects:
            if p.name == name:
                return p
        return None

    # --- End Dummy Functions ---

    # Need to import EngineState from engine.py for the DummyEngine if not defined locally
    # This creates a dependency even for standalone testing.
    # A better approach might be string-based states in dummy or duplicating the Enum.
    # Assuming EngineState enum exists and is importable:
    try:
        from engine import EngineState
    except ImportError:
        # Define Dummy EngineState if engine.py isn't available/importable
        from enum import Enum, auto
        class EngineState(Enum): IDLE=auto(); PROJECT_SELECTED=auto(); ERROR=auto(); RUNNING_WAITING_LOG=auto(); PAUSED_WAITING_USER_INPUT=auto(); RUNNING_CALLING_GEMINI=auto(); RUNNING_PROCESSING_LOG=auto(); SUMMARIZING_CONTEXT=auto(); LOADING_PROJECT=auto(); TASK_COMPLETE=auto()

    # Create dummy ConfigManager for standalone test if real one fails
    try:
         from config_manager import ConfigManager
         config_mgr = ConfigManager() # Tries to read config.ini
    except Exception as e:
         print(f"Could not initialize real ConfigManager: {e}. Using dummy.")
         config_mgr = type('obj', (object,), {'get_api_key': lambda: 'DUMMY_KEY', 'get_default_dev_logs_dir': lambda: './dev_logs', 'get_default_dev_instructions_dir': lambda: './dev_instructions'})()


    # Create DummyEngine instance
    engine_instance = DummyEngine()
    engine_instance.config = config_mgr # Assign dummy config

    # Create and run the App
    app = App(engine=engine_instance)
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()