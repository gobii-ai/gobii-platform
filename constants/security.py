"""
Security constants and limits for the platform.
These limits help prevent abuse and protect database integrity.
"""

# Persistent Agent Secrets Limits
class SecretLimits:
    """Security limits for persistent agent secrets."""
    
    # Maximum number of secrets per persistent agent
    MAX_SECRETS_PER_AGENT = 50
    
    # Maximum number of domains per persistent agent
    MAX_DOMAINS_PER_AGENT = 20
    
    # Maximum size of a single secret value in bytes
    MAX_SECRET_VALUE_BYTES = 4096  # 4KB per secret value
    
    # Maximum size of a secret key name in characters
    MAX_SECRET_KEY_LENGTH = 64
    
    # Maximum size of domain pattern in characters
    MAX_DOMAIN_PATTERN_LENGTH = 256
    
    # Maximum total size of all secrets per agent in bytes (compressed)
    MAX_TOTAL_SECRETS_SIZE_BYTES = 102400  # 100KB total per agent
    
    # Key name validation patterns
    SECRET_KEY_MIN_LENGTH = 1
    SECRET_KEY_MAX_LENGTH = MAX_SECRET_KEY_LENGTH
    
    # Domain validation patterns
    DOMAIN_MIN_LENGTH = 3  # Shortest valid domain like "a.b"
    DOMAIN_MAX_LENGTH = MAX_DOMAIN_PATTERN_LENGTH


class ValidationMessages:
    """User-friendly validation error messages."""
    
    TOO_MANY_SECRETS = f"Maximum {SecretLimits.MAX_SECRETS_PER_AGENT} secrets allowed per agent"
    TOO_MANY_DOMAINS = f"Maximum {SecretLimits.MAX_DOMAINS_PER_AGENT} domains allowed per agent"
    SECRET_VALUE_TOO_LARGE = f"Secret value too large (max {SecretLimits.MAX_SECRET_VALUE_BYTES} bytes)"
    SECRET_KEY_TOO_LONG = f"Secret key too long (max {SecretLimits.MAX_SECRET_KEY_LENGTH} characters)"
    DOMAIN_PATTERN_TOO_LONG = f"Domain pattern too long (max {SecretLimits.MAX_DOMAIN_PATTERN_LENGTH} characters)"
    TOTAL_SECRETS_TOO_LARGE = f"Total secrets size too large (max {SecretLimits.MAX_TOTAL_SECRETS_SIZE_BYTES} bytes)"
    
    SECRET_KEY_INVALID_FORMAT = "Secret key must be alphanumeric with underscores only"
    SECRET_KEY_STARTS_WITH_NUMBER = "Secret key cannot start with a number"
    SECRET_KEY_EMPTY = "Secret key cannot be empty"
    SECRET_VALUE_EMPTY = "Secret value cannot be empty"
    
    DOMAIN_PATTERN_INVALID = "Invalid domain pattern format"
    DOMAIN_PATTERN_EMPTY = "Domain pattern cannot be empty"


class SecurityPatterns:
    """Regex patterns for security validation."""
    
    # Valid secret key pattern: alphanumeric with underscores, not starting with number
    VALID_SECRET_KEY_PATTERN = r'^[a-zA-Z_][a-zA-Z0-9_]*$'
    
    # Suspicious patterns to reject in secret keys and domain patterns
    SQL_INJECTION_PATTERNS = [
        r'(union|select|insert|update|delete|drop|create|alter|exec|execute)',
        r'(script|javascript|vbscript)',
        r'(<|>|&lt;|&gt;)',
        r'(eval|function|constructor)',
    ]
    
    # Patterns that might indicate XSS attempts
    XSS_PATTERNS = [
        r'<script',
        r'javascript:',
        r'on\w+\s*=',
        r'expression\s*\(',
    ]


class DangerousSubstrings:
    """Substrings that should be rejected for security."""
    
    # These should not appear in secret keys or domain patterns
    FORBIDDEN_SUBSTRINGS = [
        '../',           # Path traversal
        '..\\',          # Windows path traversal  
        'javascript:',   # XSS
        'data:',         # Data URLs
        'vbscript:',     # VBScript
        'file://',       # File protocol
        'ftp://',        # FTP protocol
        'ldap://',       # LDAP protocol
        'gopher://',     # Gopher protocol
        'dict://',       # Dict protocol
        'php://',        # PHP wrappers
        'expect://',     # Expect protocol
        '\x00',          # Null bytes
        '\r',            # Carriage return
        '\n',            # Line feed
        '\t',            # Tab (in domain patterns)
    ]
    
    # Additional checks for secret values (more permissive than keys/domains)
    VALUE_FORBIDDEN_SUBSTRINGS = [
        '\x00',          # Null bytes
        '\r\n\r\n',      # HTTP header injection
        'HTTP/',         # HTTP protocol injection
    ] 