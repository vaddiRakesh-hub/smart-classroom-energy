"""Domain exception classes for Smart Classroom Energy system.

Provides a clean exception hierarchy for input validation, device authentication,
resource lookup, and request throttling.
"""


class SmartClassroomError(Exception):
    """Base exception for all Smart Classroom Energy application errors."""


class ValidationError(SmartClassroomError, ValueError):
    """Raised when telemetry, configuration, or API parameters fail validation."""


class AuthenticationError(SmartClassroomError):
    """Raised when a request provides an invalid or missing API key."""


class ResourceNotFoundError(SmartClassroomError, KeyError):
    """Raised when an entity such as a classroom or reading cannot be found."""


class RateLimitExceededError(SmartClassroomError):
    """Raised when an IP or client exceeds the allowed request rate."""
