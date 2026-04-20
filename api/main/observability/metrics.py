from prometheus_fastapi_instrumentator import Instrumentator


def setup_metrics(app) -> None:
    """Expose /metrics for Prometheus scraping.

    Auto-collected:
        http_requests_total{method, handler, status}   — request counter
        http_request_duration_seconds{handler}          — latency histogram

    To scrape locally:
        curl http://localhost:8000/metrics
    """
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
