import configparser
import os

class ConfigManager:
    def __init__(self, config_file='config.ini'):
        self.config_file = config_file
        self.config = configparser.ConfigParser()
        if not os.path.exists(self.config_file):
            raise FileNotFoundError(f"Configuration file '{self.config_file}' not found.")
        self.config.read(self.config_file)

    def get_api_key(self):
        try:
            return self.config.get('API', 'gemini_api_key')
        except (configparser.NoSectionError, configparser.NoOptionError) as e:
            print(f"Error reading API key: {e}")
            return None

    def get_default_dev_logs_dir(self):
        try:
            return self.config.get('PATHS', 'default_dev_logs_dir')
        except (configparser.NoSectionError, configparser.NoOptionError) as e:
            print(f"Error reading default_dev_logs_dir: {e}")
            return './dev_logs' # Default fallback

    def get_default_dev_instructions_dir(self):
        try:
            return self.config.get('PATHS', 'default_dev_instructions_dir')
        except (configparser.NoSectionError, configparser.NoOptionError) as e:
            print(f"Error reading default_dev_instructions_dir: {e}")
            return './dev_instructions' # Default fallback

# Example usage (optional, for testing)
if __name__ == '__main__':
    try:
        config_manager = ConfigManager()
        print(f"API Key: {config_manager.get_api_key()}")
        print(f"Dev Logs Dir: {config_manager.get_default_dev_logs_dir()}")
        print(f"Dev Instructions Dir: {config_manager.get_default_dev_instructions_dir()}")

        # Test missing file
        # config_manager_missing = ConfigManager('missing_config.ini')

    except FileNotFoundError as e:
        print(e)
    except Exception as e:
        print(f"An unexpected error occurred: {e}") 