#!/usr/bin/env python3
"""Minimal standard-library client for ARC Chat's local review API.

Examples:
  python examples/integration_client.py --token TOKEN status
  python examples/integration_client.py --token TOKEN artifacts
  python examples/integration_client.py --token TOKEN propose-job --summary "Analyze report.csv"

The client cannot execute code, submit/cancel Slurm jobs, or start/stop model
services. ``propose-job`` only adds a review proposal to ARC Chat Advanced Mode.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request


def call(base: str, token: str, path: str, *, method: str = "GET", body=None):
    query = urllib.parse.urlencode({"token": token})
    url = base.rstrip("/") + path + "?" + query
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--token", required=True, help="Token from the ARC Chat local URL fragment")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("artifacts")
    sub.add_parser("jobs")
    proposal = sub.add_parser("propose-job")
    proposal.add_argument("--summary", required=True)
    proposal.add_argument("--profile", default="falcon-l40s-small")
    args = parser.parse_args()

    if args.command == "propose-job":
        result = call(
            args.base,
            args.token,
            "/api/v1/proposals",
            method="POST",
            body={
                "kind": "job",
                "summary": args.summary,
                "source": "examples/integration_client.py",
                "payload": {"resource_profile": args.profile},
            },
        )
    else:
        result = call(args.base, args.token, "/api/v1/" + args.command)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
