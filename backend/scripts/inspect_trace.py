"""Phase 13: a small, developer-facing local trace inspector.

A trace's ID is always the same `thread_id` an `AgentService.run()`/
`.resume()` call was given (see agent/service.py's docstring for why),
so anything that printed a thread_id — a manual demo script, an
evaluation run, your own code — can be inspected here directly.

Traces are read from the local, credential-free `JSONFileRecorder`
directory (default `.observability/traces/`, gitignored) — no external
service involved.

Usage:
    .venv/bin/python backend/scripts/inspect_trace.py --list
    .venv/bin/python backend/scripts/inspect_trace.py <trace_id>
    .venv/bin/python backend/scripts/inspect_trace.py <trace_id> --json
    .venv/bin/python backend/scripts/inspect_trace.py <trace_id> --trace-dir /custom/path
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from observability.recorder import JSONFileRecorder
from observability.report import render_trace


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a locally recorded AI Software Engineering Agent trace.")
    parser.add_argument("trace_id", nargs="?", help="The trace_id (== thread_id) to inspect.")
    parser.add_argument("--list", action="store_true", help="List every trace_id available locally.")
    parser.add_argument("--json", action="store_true", help="Print the raw trace as JSON instead of a rendered tree.")
    parser.add_argument("--trace-dir", default=None, help="Override the trace directory (default: .observability/traces/).")
    args = parser.parse_args()

    recorder = JSONFileRecorder(args.trace_dir)

    if args.list or not args.trace_id:
        trace_ids = recorder.list_trace_ids()
        if not trace_ids:
            print(f"No traces found in {recorder.directory}")
            print("Run a script that constructs Tracer(JSONFileRecorder()) first — e.g. manual_agent_demo.py.")
        else:
            print(f"Traces in {recorder.directory}:")
            for trace_id in trace_ids:
                print(f"  {trace_id}")
        return

    trace = recorder.load_trace(args.trace_id)
    if trace is None:
        print(f"No trace found with id {args.trace_id!r} in {recorder.directory}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(_trace_to_plain_dict(trace), indent=2, default=str))
    else:
        print(render_trace(trace))


def _trace_to_plain_dict(trace) -> dict:
    data = asdict(trace)
    return data


if __name__ == "__main__":
    main()
