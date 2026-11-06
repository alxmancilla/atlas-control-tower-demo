class AppError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class BadRequest(AppError):
    def __init__(self, detail: str):
        super().__init__(400, detail)


class NotFound(AppError):
    def __init__(self, detail: str):
        super().__init__(404, detail)


class Conflict(AppError):
    def __init__(self, detail: str):
        super().__init__(409, detail)