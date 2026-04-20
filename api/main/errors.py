class ExternalServiceError(Exception):
    """Upstream API call failed (ORS, Overpass, PDOK, etc.)

    This is NOT our bug — it's the upstream service's fault.
    Logged at WARNING. Maps to HTTP 502 or 504.
    """
    def __init__(self, service: str, upstream_status: int, detail: str):
        self.service = service
        self.upstream_status = upstream_status
        self.detail = detail
        super().__init__(f"[{service}] {upstream_status}: {detail}")


class InternalError(Exception):
    """Unexpected failure in our own code.

    This IS our bug — needs investigation and alerting.
    Logged at ERROR with full stack trace. Maps to HTTP 500.
    """
    pass
