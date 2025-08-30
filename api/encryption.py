"""
Simple, secure encryption for secrets at rest.
Uses AES-256-GCM for authenticated encryption.

Supports both legacy flat secrets format and new domain-specific format:
- Legacy: {"key": "value"}
- New: {"https://example.com": {"key": "value"}}
"""
import os
import json
import logging
from typing import Dict, Union
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .domain_validation import DomainPatternValidator

logger = logging.getLogger(__name__)


class SecretsEncryption:
    """Handles encryption/decryption of secrets with domain support."""
    
    @staticmethod
    def get_encryption_key():
        """Get the master encryption key from environment."""
        key = os.environ.get('GOBII_ENCRYPTION_KEY')
        if not key:
            raise ValueError("GOBII_ENCRYPTION_KEY not configured")
        # Derive a proper 256-bit key from the env var
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'gobii-secrets-v1',  # Static salt OK for this use case
            iterations=100000,
            backend=default_backend()
        )
        return kdf.derive(key.encode())
    
    @classmethod
    def _is_domain_specific_format(cls, secrets_dict: Dict) -> bool:
        """Check if secrets dictionary uses domain-specific format."""
        if not isinstance(secrets_dict, dict) or not secrets_dict:
            return False
        
        # Check if all values are dictionaries (domain-specific format)
        # vs all values are strings (legacy flat format)
        first_value = next(iter(secrets_dict.values()))
        return isinstance(first_value, dict)
    
    @classmethod
    def validate_and_normalize_secrets(cls, secrets_dict: Union[Dict[str, str], Dict[str, Dict[str, str]]], allow_legacy: bool = False) -> Dict[str, Dict[str, str]]:
        """
        Validate and normalize secrets to domain-specific format.
        
        Args:
            secrets_dict: Either legacy flat format or domain-specific format
            allow_legacy: If True, allows legacy flat format and converts to default domain.
                         If False, requires domain-specific format.
            
        Returns:
            Domain-specific format secrets dictionary
            
        Raises:
            ValueError: If validation fails
        """
        if not secrets_dict:
            raise ValueError("Secrets dictionary cannot be empty")
        
        # Import security constants for additional validation
        from constants.security import SecretLimits, ValidationMessages
        
        if cls._is_domain_specific_format(secrets_dict):
            # New domain-specific format
            DomainPatternValidator.validate_secrets_dict(secrets_dict)
            
            # Additional size validation before normalization
            total_size = cls._estimate_secrets_size(secrets_dict)
            if total_size > SecretLimits.MAX_TOTAL_SECRETS_SIZE_BYTES:
                raise ValueError(ValidationMessages.TOTAL_SECRETS_TOO_LARGE)
            
            # Normalize domain patterns
            normalized = {}
            for domain, secrets in secrets_dict.items():
                normalized_domain = DomainPatternValidator.normalize_domain_pattern(domain)
                normalized[normalized_domain] = secrets
            return normalized
        else:
            # Legacy flat format handling
            if not allow_legacy:
                raise ValueError(
                    "Secrets must be in domain-specific format. "
                    "Use: {'https://example.com': {'x_api_key': 'value'}} "
                    "instead of: {'x_api_key': 'value'}"
                )
            
            # Legacy flat format - convert to domain-specific format with default domain
            logger.warning("Converting legacy flat secrets to domain-specific format")
            default_domain = "https://*.legacy-migrated.local"
            
            # Validate legacy secrets before conversion
            cls._validate_legacy_secrets(secrets_dict)
            
            normalized = {default_domain: secrets_dict}
            return normalized
    
    @classmethod
    def _estimate_secrets_size(cls, secrets_dict: Dict[str, Dict[str, str]]) -> int:
        """Estimate the size of secrets dictionary when serialized."""
        import json
        try:
            # Estimate size by serializing to JSON
            serialized = json.dumps(secrets_dict)
            return len(serialized.encode('utf-8'))
        except Exception:
            # Fallback estimation
            total_size = 0
            for domain, secrets in secrets_dict.items():
                total_size += len(domain.encode('utf-8'))
                for key, value in secrets.items():
                    total_size += len(key.encode('utf-8')) + len(value.encode('utf-8'))
            return total_size
    
    @classmethod
    def _validate_legacy_secrets(cls, secrets_dict: Dict[str, str]) -> None:
        """Validate legacy flat secrets format."""
        from constants.security import SecretLimits, ValidationMessages
        
        if len(secrets_dict) > SecretLimits.MAX_SECRETS_PER_AGENT:
            raise ValueError(ValidationMessages.TOO_MANY_SECRETS)
        
        for key, value in secrets_dict.items():
            DomainPatternValidator._validate_secret_key(key)
            DomainPatternValidator._validate_secret_value(value)
    
    @classmethod
    def encrypt_secrets(cls, secrets_dict: Union[Dict[str, str], Dict[str, Dict[str, str]]], allow_legacy: bool = False):
        """Encrypt a secrets dictionary. Returns encrypted bytes."""
        if not secrets_dict:
            return None
        
        try:
            # Validate and normalize to domain-specific format
            normalized_secrets = cls.validate_and_normalize_secrets(secrets_dict, allow_legacy=allow_legacy)
            
            key = cls.get_encryption_key()
            nonce = os.urandom(12)  # GCM recommended nonce size
            
            cipher = Cipher(
                algorithms.AES(key),
                modes.GCM(nonce),
                backend=default_backend()
            )
            encryptor = cipher.encryptor()
            
            plaintext = json.dumps(normalized_secrets).encode()
            ciphertext = encryptor.update(plaintext) + encryptor.finalize()
            
            # Return nonce + tag + ciphertext as single bytes
            return nonce + encryptor.tag + ciphertext
        except Exception as e:
            logger.error(f"Failed to encrypt secrets: {str(e)}")
            raise
    
    @classmethod
    def decrypt_secrets(cls, encrypted_data) -> Union[Dict[str, Dict[str, str]], None]:
        """Decrypt secrets data. Returns domain-specific secrets dictionary."""
        if not encrypted_data:
            return None
        
        try:
            key = cls.get_encryption_key()
            
            # Extract nonce, tag, and ciphertext
            nonce = encrypted_data[:12]
            tag = encrypted_data[12:28]
            ciphertext = encrypted_data[28:]
            
            cipher = Cipher(
                algorithms.AES(key),
                modes.GCM(nonce, tag),
                backend=default_backend()
            )
            decryptor = cipher.decryptor()
            
            plaintext = decryptor.update(ciphertext) + decryptor.finalize()
            secrets_dict = json.loads(plaintext.decode())
            
            # Handle legacy format by converting to domain-specific format
            if not cls._is_domain_specific_format(secrets_dict):
                # Legacy format - return as-is for backwards compatibility during migration
                # This will be handled by migration scripts
                logger.warning("Decrypted legacy flat secrets format - migration needed")
                return secrets_dict
            
            return secrets_dict
        except Exception as e:
            logger.error(f"Failed to decrypt secrets: {str(e)}")
            raise
    
    @classmethod
    def get_secret_keys_for_audit(cls, secrets_dict: Dict[str, Dict[str, str]]) -> Dict[str, list]:
        """Extract secret keys grouped by domain for audit purposes."""
        if not secrets_dict:
            return {}
        
        if cls._is_domain_specific_format(secrets_dict):
            return DomainPatternValidator.get_secret_keys_by_domain(secrets_dict)
        else:
            # Legacy format
            return {"legacy": list(secrets_dict.keys())}

    @classmethod
    def encrypt_value(cls, value: str) -> bytes:
        """
        Encrypt a single secret value.
        
        Args:
            value: Plain text secret value to encrypt
            
        Returns:
            Encrypted bytes (nonce + tag + ciphertext)
        """
        if not value:
            raise ValueError("Value cannot be empty")
        
        try:
            key = cls.get_encryption_key()
            nonce = os.urandom(12)  # GCM recommended nonce size
            
            cipher = Cipher(
                algorithms.AES(key),
                modes.GCM(nonce),
                backend=default_backend()
            )
            encryptor = cipher.encryptor()
            
            plaintext = value.encode('utf-8')
            ciphertext = encryptor.update(plaintext) + encryptor.finalize()
            
            # Return nonce + tag + ciphertext as single bytes
            return nonce + encryptor.tag + ciphertext
        except Exception as e:
            logger.error(f"Failed to encrypt value: {str(e)}")
            raise

    @classmethod
    def decrypt_value(cls, encrypted_data: bytes) -> str:
        """
        Decrypt a single secret value.
        
        Args:
            encrypted_data: Encrypted bytes (nonce + tag + ciphertext)
            
        Returns:
            Plain text secret value
        """
        if not encrypted_data:
            return ""
        
        try:
            key = cls.get_encryption_key()
            
            # Extract nonce, tag, and ciphertext
            nonce = encrypted_data[:12]
            tag = encrypted_data[12:28]
            ciphertext = encrypted_data[28:]
            
            cipher = Cipher(
                algorithms.AES(key),
                modes.GCM(nonce, tag),
                backend=default_backend()
            )
            decryptor = cipher.decryptor()
            
            plaintext = decryptor.update(ciphertext) + decryptor.finalize()
            return plaintext.decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to decrypt value: {str(e)}")
            raise 