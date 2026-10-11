"""Application errors raised by services and translated to HTTP responses."""


class AppError(Exception):
    """Base class for expected, client-facing service errors."""

    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409


class DomainValidationError(AppError):
    status_code = 422


class ForbiddenError(AppError):
    status_code = 403


class UnauthorizedError(AppError):
    status_code = 401
