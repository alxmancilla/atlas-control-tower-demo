// Errors that carry an HTTP status, so the API can tell caller mistakes (4xx) from server faults (5xx).
export class StatusError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export class BadRequestError extends StatusError {
  constructor(message) {
    super(400, message);
  }
}

export class NotFoundError extends StatusError {
  constructor(message) {
    super(404, message);
  }
}

export class ConflictError extends StatusError {
  constructor(message) {
    super(409, message);
  }
}
