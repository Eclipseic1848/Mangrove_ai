# -*- coding: utf-8 -*-
"""批次 8A：通过真实 OTLP HTTP 接收端验证工作台轨迹。"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from src.observability.workspace_telemetry import (
    configure_workspace_telemetry,
    force_flush_workspace_telemetry,
    shutdown_workspace_telemetry,
    workspace_stage_span,
    workspace_task_span,
)


def test_workspace_spans_reach_otlp_receiver_without_business_text():
    requests: list[bytes] = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            requests.append(self.rfile.read(length))
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/v1/traces"

    try:
        configure_workspace_telemetry(endpoint=endpoint)
        with workspace_task_span(
            task_id="workspace-sensitive-id",
            revision=2,
            source_types=["docx"],
            source_count=1,
            output_formats=["txt"],
            provider="local",
            model="local-fixture",
        ):
            with workspace_stage_span(
                "compile",
                status="failed",
                error_code="STP_COMPILE_FAILED",
                retry_count=2,
            ):
                pass
        assert force_flush_workspace_telemetry(timeout_millis=5_000)
    finally:
        shutdown_workspace_telemetry()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert requests
    spans = []
    for body in requests:
        exported = ExportTraceServiceRequest()
        exported.ParseFromString(body)
        spans.extend(
            span
            for resource in exported.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
        )
    assert {span.name for span in spans} == {
        "workspace.task",
        "workspace.compile",
    }
    assert len({span.trace_id for span in spans}) == 1
    attributes = {
        item.key: (
            item.value.string_value
            or item.value.int_value
            or item.value.bool_value
        )
        for span in spans
        for item in span.attributes
    }
    assert attributes["workspace.task_id_hash"] != "workspace-sensitive-id"
    assert len(attributes["workspace.task_id_hash"]) == 64
    assert attributes["workspace.revision"] == 2
    assert attributes["workspace.error_code"] == "STP_COMPILE_FAILED"
    serialized = b"".join(requests)
    assert b"workspace-sensitive-id" not in serialized
