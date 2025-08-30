"""
Domain validation utilities for persistent agent secrets.

Implements domain pattern validation consistent with browser_use library
for restricting secrets to specific websites.
"""
import re
from typing import Dict, List


class DomainPatternValidator:
    """Validates domain patterns for secrets restrictions."""
    
    @staticmethod
    def validate_domain_pattern(pattern: str) -> None:
        """
        Validate a domain pattern according to browser_use rules.
        
        Args:
            pattern: Domain pattern to validate
            
        Raises:
            ValueError: If the pattern is invalid
        """
        if not pattern or not isinstance(pattern, str):
            raise ValueError("Domain pattern must be a non-empty string")
        
        pattern = pattern.strip()
        if not pattern:
            raise ValueError("Domain pattern cannot be empty")
        
        # Import security constants for validation
        from constants.security import SecretLimits, DangerousSubstrings, SecurityPatterns
        
        # Length validation
        if len(pattern) > SecretLimits.MAX_DOMAIN_PATTERN_LENGTH:
            raise ValueError(f"Domain pattern too long (max {SecretLimits.MAX_DOMAIN_PATTERN_LENGTH} characters)")
        
        if len(pattern) < SecretLimits.DOMAIN_MIN_LENGTH:
            raise ValueError(f"Domain pattern too short (min {SecretLimits.DOMAIN_MIN_LENGTH} characters)")
        
        # Security validation - check for dangerous substrings
        pattern_lower = pattern.lower()
        for forbidden in DangerousSubstrings.FORBIDDEN_SUBSTRINGS:
            if forbidden in pattern_lower:
                raise ValueError(f"Domain pattern contains forbidden substring: {forbidden}")
        
        # Check for SQL injection patterns
        for sql_pattern in SecurityPatterns.SQL_INJECTION_PATTERNS:
            if re.search(sql_pattern, pattern_lower, re.IGNORECASE):
                raise ValueError("Domain pattern contains suspicious content")
        
        # Check for XSS patterns
        for xss_pattern in SecurityPatterns.XSS_PATTERNS:
            if re.search(xss_pattern, pattern_lower, re.IGNORECASE):
                raise ValueError("Domain pattern contains suspicious content")
        
        # Extract domain part for validation
        domain_part = pattern
        if '://' in pattern:
            domain_part = pattern.split('://', 1)[1]
        
        # Reject wildcards in TLD part (e.g., example.*, *.com)
        if re.search(r'\.\*$', domain_part):
            raise ValueError("Wildcards in TLD part are not allowed (e.g., google.* would match google.ninja)")
        
        # Special case: reject patterns that are just a wildcard TLD like *.com
        if re.match(r'^\*\.[^.]+$', domain_part):
            raise ValueError("Wildcards in TLD part are not allowed (e.g., *.com would match any .com domain)")
        
        # Reject embedded wildcards (e.g., g*e.com)
        if re.search(r'[^/]\*[^/]', pattern):
            raise ValueError("Embedded wildcards are not supported (e.g., g*e.com)")
        
        # Reject multiple wildcards like *.*.domain
        if pattern.count('*') > 1:
            raise ValueError("Multiple wildcards like *.*.domain are not supported")
        
        # Validate protocol if present
        if '://' in pattern:
            parts = pattern.split('://', 1)
            protocol, domain_part = parts
            
            if protocol not in ['http', 'https', 'http*', 'chrome-extension']:
                raise ValueError(f"Invalid protocol '{protocol}'. Only http, https, http*, and chrome-extension are supported")
        
        # Validate chrome-extension patterns
        if pattern.startswith('chrome-extension://'):
            extension_part = pattern[19:]  # Remove 'chrome-extension://'
            if not extension_part:
                raise ValueError("Chrome extension ID cannot be empty")
    
    @staticmethod
    def validate_secrets_dict(secrets_dict: Dict[str, Dict[str, str]]) -> None:
        """
        Validate a complete domain-specific secrets dictionary.
        
        Args:
            secrets_dict: Dictionary mapping domains to secret key-value pairs
            
        Raises:
            ValueError: If the structure or domains are invalid
        """
        if not isinstance(secrets_dict, dict):
            raise ValueError("Secrets must be a dictionary")
        
        if not secrets_dict:
            raise ValueError("Secrets dictionary cannot be empty")
        
        # Import security constants
        from constants.security import SecretLimits, ValidationMessages, SecurityPatterns, DangerousSubstrings
        
        # Check global limits
        total_secrets = sum(len(domain_secrets) for domain_secrets in secrets_dict.values())
        if total_secrets > SecretLimits.MAX_SECRETS_PER_AGENT:
            raise ValueError(ValidationMessages.TOO_MANY_SECRETS)
        
        if len(secrets_dict) > SecretLimits.MAX_DOMAINS_PER_AGENT:
            raise ValueError(ValidationMessages.TOO_MANY_DOMAINS)
        
        for domain_pattern, secrets in secrets_dict.items():
            # Validate domain pattern
            DomainPatternValidator.validate_domain_pattern(domain_pattern)
            
            # Validate secrets for this domain
            if not isinstance(secrets, dict):
                raise ValueError(f"Secrets for domain '{domain_pattern}' must be a dictionary")
            
            if not secrets:
                raise ValueError(f"Secrets for domain '{domain_pattern}' cannot be empty")
            
            for key, value in secrets.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    raise ValueError(f"All secret keys and values must be strings in domain '{domain_pattern}'")
                
                DomainPatternValidator._validate_secret_key(key)
                DomainPatternValidator._validate_secret_value(value)
    
    @staticmethod
    def _validate_secret_key(key: str) -> None:
        """Validate a secret key meets security requirements."""
        from constants.security import SecretLimits, ValidationMessages, SecurityPatterns, DangerousSubstrings
        
        # Length validation
        if len(key) > SecretLimits.MAX_SECRET_KEY_LENGTH:
            raise ValueError(ValidationMessages.SECRET_KEY_TOO_LONG)
        
        if not key.strip():
            raise ValueError(ValidationMessages.SECRET_KEY_EMPTY)
        
        # Format validation
        if not re.match(SecurityPatterns.VALID_SECRET_KEY_PATTERN, key):
            raise ValueError(ValidationMessages.SECRET_KEY_INVALID_FORMAT)
        
        # Check for dangerous substrings
        key_lower = key.lower()
        for forbidden in DangerousSubstrings.FORBIDDEN_SUBSTRINGS:
            if forbidden in key_lower:
                raise ValueError(f"Secret key contains forbidden substring: {forbidden}")
        
        # Check for SQL injection patterns
        for pattern in SecurityPatterns.SQL_INJECTION_PATTERNS:
            if re.search(pattern, key_lower, re.IGNORECASE):
                raise ValueError("Secret key contains suspicious pattern")
    
    @staticmethod
    def _validate_secret_value(value: str) -> None:
        """Validate a secret value meets security requirements."""
        from constants.security import SecretLimits, ValidationMessages, DangerousSubstrings
        
        # Size validation
        value_bytes = len(value.encode('utf-8'))
        if value_bytes > SecretLimits.MAX_SECRET_VALUE_BYTES:
            raise ValueError(ValidationMessages.SECRET_VALUE_TOO_LARGE)
        
        if not value.strip():
            raise ValueError(ValidationMessages.SECRET_VALUE_EMPTY)
        
        # Check for dangerous substrings in values (more permissive than keys)
        for forbidden in DangerousSubstrings.VALUE_FORBIDDEN_SUBSTRINGS:
            if forbidden in value:
                raise ValueError("Secret value contains forbidden content")
    
    @staticmethod
    def normalize_domain_pattern(pattern: str) -> str:
        """
        Normalize a domain pattern for consistent storage.
        
        Args:
            pattern: Domain pattern to normalize
            
        Returns:
            Normalized domain pattern
        """
        pattern = pattern.strip()
        
        # Add https:// if no protocol specified for security
        # Don't add protocol for chrome-extension patterns
        if not pattern.startswith(('http://', 'https://', 'chrome-extension://')):
            pattern = f'https://{pattern}'
        
        return pattern
    
    @staticmethod
    def get_secret_keys_by_domain(secrets_dict: Dict[str, Dict[str, str]]) -> Dict[str, List[str]]:
        """
        Extract secret keys grouped by domain for audit purposes.
        
        Args:
            secrets_dict: Domain-specific secrets dictionary
            
        Returns:
            Dictionary mapping domains to lists of secret keys
        """
        return {
            domain: list(secrets.keys())
            for domain, secrets in secrets_dict.items()
        } 