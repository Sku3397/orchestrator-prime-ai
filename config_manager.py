import configparser
import os
from typing import Optional, Any
import logging

# Get logger instance
logger = logging.getLogger("orchestrator_prime")

class ConfigManager:
    def __init__(self, config_file='config.ini'):
        self.config_file = config_file
        self.config = configparser.ConfigParser()
        if not os.path.exists(self.config_file):
            logger.warning(f"Configuration file '{self.config_file}' not found. Creating default.")
            self._create_default_config()
        else:
            logger.debug(f"Reading configuration from {self.config_file}")
            try:
                self.config.read(self.config_file)
            except configparser.Error as e:
                logger.error(f"Failed to parse configuration file '{self.config_file}': {e}", exc_info=True)
                # Consider raising an exception or setting a failure state
                # For now, we continue and default sections might be created.

        # Ensure critical sections exist after attempting read/create
        if not self.config.has_section('API'):
            logger.warning("Config section 'API' missing. Adding default.")
            self.config.add_section('API')
            # Add default key/value pairs needed for API section
            if not self.config.has_option('API', 'gemini_api_key'):
                self.config.set('API', 'gemini_api_key', 'YOUR_API_KEY_HERE')
            if not self.config.has_option('API', 'gemini_model'):
                self.config.set('API', 'gemini_model', 'gemini-1.5-flash-latest')
        
        if not self.config.has_section('PATHS'):
            logger.warning("Config section 'PATHS' missing. Adding default.")
            self.config.add_section('PATHS')
            if not self.config.has_option('PATHS', 'default_dev_logs_dir'):
                self.config.set('PATHS', 'default_dev_logs_dir', './dev_logs')
            if not self.config.has_option('PATHS', 'default_dev_instructions_dir'):
                self.config.set('PATHS', 'default_dev_instructions_dir', './dev_instructions')

    def _create_default_config(self):
        self.config['API'] = {
            'gemini_api_key': 'YOUR_API_KEY_HERE',
            'gemini_model': 'gemini-1.5-flash-latest' # Add default model here
        }
        self.config['PATHS'] = {
            'default_dev_logs_dir': './dev_logs',
            'default_dev_instructions_dir': './dev_instructions'
        }
        self.config['GEMINI_CONTEXT'] = {
            'max_history_turns': '20',
            'max_context_tokens': '30000', # Check model limits
            'max_summary_tokens': '1000'
        }
        self.config['ENGINE_CONFIG'] = {
            'cursor_log_timeout_seconds': '300', # 5 minutes
            'log_file_read_delay_seconds': '0.5',
            'watchdog_debounce_seconds': '2.0'
        }
        self.config['SETTINGS'] = {
            'summary_interval': '10' # Summarize every 10 turns
        }
        self.config['STRUCTURE_ANALYSIS'] = {
             'max_files': '10',
             'max_dirs': '10',
             'excluded_patterns': '.git,__pycache__,node_modules,.venv,venv,.idea,.vscode' # Comma-separated
        }
        try:
            with open(self.config_file, 'w') as f:
                self.config.write(f)
            logger.info(f"Default configuration file '{self.config_file}' created.")
        except IOError as e:
            logger.error(f"Could not write default config file '{self.config_file}': {e}", exc_info=True)

    def get_api_key(self) -> Optional[str]:
        key = self.get_config_value('API', 'gemini_api_key', fallback=None)
        if key == 'YOUR_API_KEY_HERE':
            logger.warning("API Key is set to placeholder 'YOUR_API_KEY_HERE' in config.ini")
            return None
        return key

    def set_api_key(self, api_key: str) -> bool:
        return self.set_config_value('API', 'gemini_api_key', api_key)

    def get_default_dev_logs_dir(self) -> str:
        return self.get_config_value('PATHS', 'default_dev_logs_dir', fallback='./dev_logs')

    def get_default_dev_instructions_dir(self) -> str:
        return self.get_config_value('PATHS', 'default_dev_instructions_dir', fallback='./dev_instructions')
        
    def get_gemini_model(self) -> str:
        return self.get_config_value('API', 'gemini_model', fallback='gemini-1.5-flash-latest')
    
    def get_max_output_tokens_gemini(self) -> int:
         return self.config.getint('GEMINI_CONTEXT', 'max_output_tokens', fallback=8192)
         
    def get_temperature_gemini(self) -> float:
         return self.config.getfloat('GEMINI_CONTEXT', 'temperature', fallback=0.7)

    def get_max_history_turns(self) -> int:
        value = self.get_config_value('GEMINI_CONTEXT', 'max_history_turns', fallback='20')
        try:
            return int(value)
        except (ValueError, TypeError):
            logger.warning(f"Invalid value '{value}' for max_history_turns in config. Using default 20.")
            return 20

    def get_max_context_tokens(self) -> int:
        value = self.get_config_value('GEMINI_CONTEXT', 'max_context_tokens', fallback='30000')
        try:
            return int(value)
        except (ValueError, TypeError):
            logger.warning(f"Invalid value '{value}' for max_context_tokens in config. Using default 30000.")
            return 30000
            
    def get_max_summary_tokens(self) -> int:
         value = self.get_config_value('GEMINI_CONTEXT', 'max_summary_tokens', fallback='1000')
         try:
             return int(value)
         except (ValueError, TypeError):
             logger.warning(f"Invalid value '{value}' for max_summary_tokens in config. Using default 1000.")
             return 1000

    def get_cursor_log_timeout_seconds(self) -> int:
        return self.config.getint('ENGINE_CONFIG', 'cursor_log_timeout_seconds', fallback=300)
        
    def get_log_file_read_delay_seconds(self) -> float:
        return self.config.getfloat('ENGINE_CONFIG', 'log_file_read_delay_seconds', fallback=0.5)
        
    def get_watchdog_debounce_seconds(self) -> float:
        return self.config.getfloat('ENGINE_CONFIG', 'watchdog_debounce_seconds', fallback=2.0)

    def get_summarization_interval(self) -> int:
        return self.config.getint('SETTINGS', 'summary_interval', fallback=10)
        
    def get_next_step_filename(self) -> str:
        return self.get_config_value('PATHS', 'next_step_filename', fallback='next_step.txt')
        
    def get_cursor_output_filename(self) -> str:
        return self.get_config_value('PATHS', 'cursor_output_filename', fallback='cursor_step_output.txt')
        
    def get_structure_max_files(self) -> int:
         return self.config.getint('STRUCTURE_ANALYSIS', 'max_files', fallback=10)
         
    def get_structure_max_dirs(self) -> int:
         return self.config.getint('STRUCTURE_ANALYSIS', 'max_dirs', fallback=10)
         
    def get_structure_excluded_patterns(self) -> List[str]:
         patterns_str = self.get_config_value('STRUCTURE_ANALYSIS', 'excluded_patterns', fallback='.git,__pycache__,node_modules,.venv,venv,.idea,.vscode')
         return [p.strip() for p in patterns_str.split(',') if p.strip()]

    def get_config_value(self, section: str, option: str, fallback: Any = None) -> Any:
        try:
            if not self.config.has_section(section):
                 logger.warning(f"Config section '{section}' not found. Using fallback '{fallback}' for option '{option}'.")
                 if fallback is not None:
                     self._ensure_config_section_option(section, option, fallback)
                 return fallback 

            # Use get methods that handle type conversion and fallback directly
            # This avoids needing explicit fallback logic here in many cases.
            # However, for custom logic or non-standard types, direct .get() might be needed.
            # Sticking with .get() for consistency with previous code for now.
            return self.config.get(section, option, fallback=fallback) # configparser handles fallback if option missing
        
        except Exception as e:
             logger.error(f"Unexpected error getting config value [{section}] {option}: {e}", exc_info=True)
             return fallback # Return fallback on unexpected error

    def _ensure_config_section_option(self, section: str, option: str, fallback: Any):
        needs_save = False
        if not self.config.has_section(section):
             self.config.add_section(section)
             logger.info(f"Added missing section '{section}' to config.")
             needs_save = True
        if not self.config.has_option(section, option):
             self.config.set(section, option, str(fallback))
             logger.info(f"Added missing option '{option}' = '{fallback}' to section '{section}'.")
             needs_save = True
        
        if needs_save:
            self.save_config()

    def set_config_value(self, section: str, option: str, value: str) -> bool:
        try:
            if not self.config.has_section(section):
                self.config.add_section(section)
                logger.info(f"Added section '{section}' to save value for '{option}'.")
            self.config.set(section, option, value)
            self.save_config()
            logger.info(f"Config value saved: [{section}] {option} = {value}")
            return True
        except Exception as e:
            logger.error(f"Error saving config value [{section}] {option} = {value}: {e}", exc_info=True)
            return False

    def save_config(self):
        """Saves the current config state to the file."""
        try:
            with open(self.config_file, 'w') as f:
                self.config.write(f)
            logger.debug(f"Configuration saved to {self.config_file}")
        except IOError as e:
            logger.error(f"Could not save config file {self.config_file}: {e}", exc_info=True)
            # Consider raising an error if saving is critical

# Example usage
if __name__ == '__main__':
    try:
        # Test with potentially missing file
        if os.path.exists('test_config.ini'): os.remove('test_config.ini')
        config_manager = ConfigManager('test_config.ini')
        
        print(f"Initial API Key: {config_manager.get_api_key()}")
        config_manager.set_api_key("test_new_key_123")
        print(f"Updated API Key: {config_manager.get_api_key()}")
        
        print(f"Dev Logs Dir: {config_manager.get_default_dev_logs_dir()}")
        print(f"Gemini Model: {config_manager.get_gemini_model()}")
        config_manager.set_config_value("API", "gemini_model", "gemini-pro")
        print(f"Updated Gemini Model: {config_manager.get_gemini_model()}")

        if os.path.exists('test_config.ini'): os.remove('test_config.ini')

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc() 