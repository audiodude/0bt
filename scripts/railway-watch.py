#!/usr/bin/env python3
"""Poll the latest deployment for a Railway service until it succeeds or fails."""
import json
import os
import sys
import time
import urllib.request
import urllib.error

API = "https://backboard.railway.com/graphql/v2"


def gql(q: str, v: dict | None = None) -> dict:
    token = os.environ["RAILWAY_TOKEN"]
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": q, "variables": v or {}}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "curl/8.5.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP {e.code}: {e.read().decode(errors='replace')}\n")
        raise
    if body.get("errors"):
        sys.stderr.write(json.dumps(body["errors"], indent=2) + "\n")
        raise SystemExit(2)
    return body["data"]


def latest_deployment(project_id: str, env_id: str, service_id: str) -> dict | None:
    res = gql(
        "query($pid: String!, $eid: String!, $sid: String!) {"
        "  deployments(input: { projectId: $pid, environmentId: $eid, serviceId: $sid }, first: 1) {"
        "    edges { node { id status createdAt url staticUrl meta } } } }",
        {"pid": project_id, "eid": env_id, "sid": service_id},
    )
    edges = res["deployments"]["edges"]
    return edges[0]["node"] if edges else None


def main() -> int:
    project_id = sys.argv[1]
    env_id = sys.argv[2]
    service_id = sys.argv[3]
    deadline = time.time() + 1200  # 20 min
    last = None
    while time.time() < deadline:
        d = latest_deployment(project_id, env_id, service_id)
        if d is None:
            print("(no deployment yet)")
        else:
            status = d["status"]
            if status != last:
                print(f"[{time.strftime('%H:%M:%S')}] status={status} url={d.get('staticUrl') or d.get('url')}")
                last = status
            if status in {"SUCCESS", "FAILED", "CRASHED", "REMOVED"}:
                return 0 if status == "SUCCESS" else 1
        time.sleep(8)
    print("timeout")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
