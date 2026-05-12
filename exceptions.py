import json


class APIError(Exception):

    def __init__(self, msg, status_code):
        Exception.__init__(self, msg)
        self.status_code = status_code
        try:
            message = json.loads(msg)
        except Exception:
            message = str(msg)
        self.message = {"error": self.__class__.__name__, "message": message}


class IoTBackendAPIError(APIError):

    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class InvalidInputError(APIError):

    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class ValidationError(APIError):

    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class ModuleConfigNotSupported(APIError):

    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class InvalidCredentials(APIError):

    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class UnmatchedDependency(APIError):

    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class SEMSError(APIError):

    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class InsufficientPermissions(APIError):

    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)

class InsufficientPermissionsOrResourceNotFound(APIError):
    pass

class UserNotFound(InsufficientPermissions):
    def __init__(self, status_code):
        InsufficientPermissions.__init__(self, "User ID not found in auth context.", status_code)


class InvalidUserRole(APIError):

    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class EdgeModuleAPIError(APIError):

    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class RequestTimeout(APIError):

    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class SEMSTemplateError(APIError):

    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class SEMSFirmwareError(APIError):

    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class ModuleNotFound(APIError):
    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class ConfigAlreadyExists(APIError):
    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class UploadError(APIError):
    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)


class BlobError(APIError):
    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)

class ConfigGenerationError(APIError):
    def __init__(self, msg, status_code):
        APIError.__init__(self, msg, status_code)

class AuthImplementationError(APIError):
    def __init__(self):
        APIError.__init__(self, "Internal Api Error", 500)
