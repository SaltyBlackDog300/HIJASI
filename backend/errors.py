class ApiError(Exception):
    def __init__(self, status, code, message, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def to_dict(self):
        return {"error": {"code": self.code, "message": self.message, **self.extra}}
