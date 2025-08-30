"""
Utilities for generating secret keys from display names.
Provides clean, deterministic key generation with uniqueness validation.
"""
import re
from typing import Set


class SecretKeyGenerator:
    """Generates secret keys from human-readable names."""
    
    @staticmethod
    def generate_key_from_name(name: str) -> str:
        """
        Generate a secret key from a display name.
        
        Examples:
        - "X Password" -> "x_password"
        - "API Key for Service" -> "api_key_for_service"
        - "Database Username" -> "database_username"
        - "My Super Secret Token!" -> "my_super_secret_token"
        
        Args:
            name: Human-readable name for the secret
            
        Returns:
            A valid secret key (alphanumeric with underscores, lowercase)
        """
        if not name or not isinstance(name, str):
            raise ValueError("Name must be a non-empty string")
        
        # Convert to lowercase and replace spaces/special chars with underscores
        key = re.sub(r'[^a-zA-Z0-9_]', '_', name.lower().strip())
        
        # Collapse multiple underscores into single ones
        key = re.sub(r'_+', '_', key)
        
        # Remove leading/trailing underscores
        key = key.strip('_')
        
        # Ensure it doesn't start with a number
        if key and key[0].isdigit():
            key = f"secret_{key}"
        
        # Fallback if empty after processing
        if not key:
            key = "secret"
        
        # Ensure minimum length of 1 character
        if len(key) < 1:
            key = "secret"
            
        return key
    
    @staticmethod
    def ensure_unique_key(base_key: str, existing_keys: Set[str]) -> str:
        """
        Ensure a key is unique by appending a number if needed.
        
        Args:
            base_key: The base key to make unique
            existing_keys: Set of already existing keys
            
        Returns:
            A unique key
        """
        if base_key not in existing_keys:
            return base_key
        
        counter = 2
        while f"{base_key}_{counter}" in existing_keys:
            counter += 1
        
        return f"{base_key}_{counter}"
    
    @classmethod
    def generate_unique_key_from_name(cls, name: str, existing_keys: Set[str]) -> str:
        """
        Generate a unique secret key from a display name.
        
        Args:
            name: Human-readable name for the secret
            existing_keys: Set of existing keys to avoid conflicts
            
        Returns:
            A unique, valid secret key
        """
        base_key = cls.generate_key_from_name(name)
        return cls.ensure_unique_key(base_key, existing_keys) 