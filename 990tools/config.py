"""
Configuration management for IRS 990 Tools.

This module handles default configuration values and can load from config files.
"""

import os
import json
from pathlib import Path

# Default configuration
DEFAULT_CONFIG = {
    "directories": {
        "zips": "./irs_zips",
        "tsvs": "./tsvs",
        "analyzed": "./analyzed",
        "final": "./final",
        "browse": "./browse",
        "cache": "./cache"
    },
    "processing": {
        "start_year": 2017,
        "end_year": 2025,
        "minimum_d": 10000000,
        "worker_threads": 16,
        "batch_size": 500,
        "write_buffer_size": 10000,
        "writer_threads": 1
    },
    "filters": {
        "org_types": ["all"],
        "not_types": [],
        "filter_column": "denominator",
        "filter_value": 1000000
    }
}

def load_config(config_file=None):
    """Load configuration from file, with defaults as fallback."""
    config = DEFAULT_CONFIG.copy()

    if config_file and Path(config_file).exists():
        try:
            with open(config_file, 'r') as f:
                user_config = json.load(f)
            # Deep merge user config with defaults
            config = merge_configs(config, user_config)
        except Exception as e:
            print(f"Warning: Could not load config file {config_file}: {e}")
            print("Using default configuration.")

    return config

def merge_configs(base, update):
    """Deep merge two configuration dictionaries."""
    result = base.copy()
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_configs(result[key], value)
        else:
            result[key] = value
    return result

def get_config_value(config, *keys):
    """Get a nested configuration value."""
    current = config
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current

def save_default_config(filename="irs990tools_config.json"):
    """Save the default configuration to a file."""
    with open(filename, 'w') as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)
    print(f"Default configuration saved to {filename}")

# Global configuration instance
_current_config = None

def get_config(config_file=None):
    """Get the current configuration, loading if necessary."""
    global _current_config
    if _current_config is None or config_file:
        _current_config = load_config(config_file)
    return _current_config

def set_config_value(*keys, value):
    """Set a configuration value."""
    config = get_config()
    current = config
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value