from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider, sampling
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from runnerforge.config import GCP_PROJECT_ID


def setup_tracing(app: FastAPI) -> None:
    resource = Resource.create(
        attributes={
            # Use the PID as the service.instance.id to avoid duplicate timeseries from different Gunicorn worker processes.
            SERVICE_NAME: "runnerforge-orchestrator",
            # SERVICE_INSTANCE_ID: f"worker-{os.getpid()}",
        }
    )

    # Provider
    sampler = sampling.ALWAYS_ON
    tracer_provider = TracerProvider(resource=resource, sampler=sampler)
    processor = BatchSpanProcessor(CloudTraceSpanExporter(project_id=GCP_PROJECT_ID))
    tracer_provider.add_span_processor(processor)
    trace.set_tracer_provider(tracer_provider)

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
