import configparser
import os
from typing import Optional, Any

class ConfigManager:
    def __init__(self, config_file='config.ini'):
        self.config_file = config_file
        self.config = configparser.ConfigParser()
        if not os.path.exists(self.config_file):
            # If config file doesn't exist, create a default one for resilience
            print(f"Warning: Configuration file '{self.config_file}' not found. Creating default.")
            self._create_default_config()
        
        self.config.read(self.config_file)
        # Ensure sections exist after reading/creating
        if not self.config.has_section('API'):
            self.config.add_section('API')
        if not self.config.has_section('PATHS'):
            self.config.add_section('PATHS')

    def _create_default_config(self):
        self.config['API'] = {'gemini_api_key': 'YOUR_API_KEY_HERE'}
        self.config['PATHS'] = {
            'default_dev_logs_dir': './dev_logs',
            'default_dev_instructions_dir': './dev_instructions',
            'gemini_model': 'gemini-1.5-flash-latest' # Add default model here
        }
        try:
            with open(self.config_file, 'w') as f:
                self.config.write(f)
            print(f"Default configuration file '{self.config_file}' created.")
        except IOError as e:
            print(f"ERROR: Could not write default config file '{self.config_file}': {e}")
            # If we can't write the default config, it's a significant issue.
            # The application might still fail later if it expects to read it.
            # For now, we let it proceed, and subsequent reads might fail.

    def get_api_key(self) -> Optional[str]:
        return self.get_config_value('API', 'gemini_api_key', fallback=None)

    def set_api_key(self, api_key: str) -> bool:
        return self.set_config_value('API', 'gemini_api_key', api_key)

    def get_default_dev_logs_dir(self) -> str:
        return self.get_config_value('PATHS', 'default_dev_logs_dir', fallback='./dev_logs')

    def get_default_dev_instructions_dir(self) -> str:
        return self.get_config_value('PATHS', 'default_dev_instructions_dir', fallback='./dev_instructions')
        
    def get_gemini_model(self) -> str: # For GeminiCommunicator
        return self.get_config_value('API', 'gemini_model', fallback='gemini-1.5-flash-latest')

    def get_max_history_turns(self) -> int:
        value = self.get_config_value('GEMINI_CONTEXT', 'max_history_turns', fallback='10')
        try:
            return int(value)
        except (ValueError, TypeError):
            print(f"Warning: Invalid value '{value}' for max_history_turns. Using default 10.")
            return 10

    def get_max_context_tokens(self) -> int:
        value = self.get_config_value('GEMINI_CONTEXT', 'max_context_tokens', fallback='30000')
        try:
            return int(value)
        except (ValueError, TypeError):
            print(f"Warning: Invalid value '{value}' for max_context_tokens. Using default 30000.")
            return 30000

    def get_config_value(self, section: str, option: str, fallback: Any = None) -> Any:
        try:
            # Ensure section exists before getting value
            if not self.config.has_section(section):
                 # If the section doesn't exist even after read/create, use fallback
                 print(f"Warning: Section '{section}' not found. Using fallback for option '{option}'.")
                 # Optionally add section and option with fallback here if desired
                 if fallback is not None:
                     self._ensure_config_section_option(section, option, fallback)
                 return fallback 

            return self.config.get(section, option)
        # except (configparser.NoSectionError, configparser.NoOptionError): # Covered by has_section check
        except configparser.NoOptionError:
            print(f"Warning: Option '{option}' not found in section '{section}'. Using fallback: {fallback}")
            # If fallback is used, and it's a critical value, we might want to write it to config
            if fallback is not None:
                 self._ensure_config_section_option(section, option, fallback)
            return fallback

    # Helper to ensure section/option exist and potentially save fallback
    def _ensure_config_section_option(self, section: str, option: str, fallback: Any):
         if not self.config.has_section(section):
             self.config.add_section(section)
             print(f"Info: Added missing section '{section}' to config.")
         if not self.config.has_option(section, option):
             self.config.set(section, option, str(fallback)) # Ensure value is string
             print(f"Info: Added missing option '{option}' = '{fallback}' to section '{section}'. Saving config.")
             try:
                 with open(self.config_file, 'w') as f:
                     self.config.write(f)
             except IOError as e:
                 print(f"ERROR: Could not save updated config file {self.config_file} after adding fallback: {e}")
                 # pass # Non-critical if saving fallback fails here

    def set_config_value(self, section: str, option: str, value: str) -> bool:
        try:
            if not self.config.has_section(section):
                self.config.add_section(section)
            self.config.set(section, option, value)
            with open(self.config_file, 'w') as f:
                self.config.write(f)
            print(f"Config value saved: [{section}] {option} = {value}")
            return True
        except (IOError, configparser.Error) as e:
            print(f"Error saving config value [{section}] {option} to {self.config_file}: {e}")
            return False

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