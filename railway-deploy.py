#!/usr/bin/env python3
"""Deploy 0bt to Railway via the GraphQL API.

Reads RAILWAY_TOKEN from the environment. Idempotent-ish: it will create a new
service if one named ``app`` does not already exist in the project, otherwise
returns the existing service.

Run from inside the VM. Re-runnable to re-issue the redeploy command.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.request

API = "https://backboard.railway.com/graphql/v2"


def gql(query: str, variables: dict | None = None) -> dict:
    token = os.environ.get("RAILWAY_TOKEN")
    if not token:
        sys.exit("RAILWAY_TOKEN not set")
    data = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            # Railway is fronted by Cloudflare; the default Python user-agent
            # gets WAF-blocked (error 1010). Pretend to be curl.
            "User-Agent": "curl/8.5.0",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP {e.code} from Railway:\n{e.read().decode(errors='replace')}\n")
        raise
    if body.get("errors"):
        raise SystemExit(f"GraphQL error: {json.dumps(body['errors'], indent=2)}")
    return body["data"]


def get_project(project_id: str) -> dict:
    """Some scoped tokens 403 on the singular `project(id)` field; fall back to
    listing and filtering."""
    res = gql(
        "{ projects { edges { node { id name services { edges { node { id name } } }"
        " volumes { edges { node { id name volumeInstances { edges { node { id"
        " mountPath serviceId environmentId } } } } } } } } } }"
    )
    for e in res["projects"]["edges"]:
        if e["node"]["id"] == project_id:
            return e["node"]
    raise SystemExit(f"project {project_id} not visible to this token")


def find_or_create_service(project_id: str, env_id: str, repo: str, branch: str, variables: dict) -> str:
    proj = get_project(project_id)
    for e in proj["services"]["edges"]:
        if e["node"]["name"] == "app":
            return e["node"]["id"]
    res = gql(
        "mutation ServiceCreate($input: ServiceCreateInput!) { serviceCreate(input: $input) { id name } }",
        {
            "input": {
                "projectId": project_id,
                "environmentId": env_id,
                "name": "app",
                "branch": branch,
                "source": {"repo": repo},
                "variables": variables,
            }
        },
    )
    return res["serviceCreate"]["id"]


def upsert_var(project_id: str, env_id: str, service_id: str, name: str, value: str) -> None:
    gql(
        "mutation Up($input: VariableUpsertInput!) { variableUpsert(input: $input) }",
        {
            "input": {
                "projectId": project_id,
                "environmentId": env_id,
                "serviceId": service_id,
                "name": name,
                "value": value,
            }
        },
    )


def get_or_make_volume(project_id: str, env_id: str, service_id: str, mount: str = "/data") -> str:
    proj = get_project(project_id)
    for e in proj["volumes"]["edges"]:
        v = e["node"]
        for vi in v["volumeInstances"]["edges"]:
            if vi["node"]["serviceId"] == service_id and vi["node"]["mountPath"] == mount:
                return v["id"]
    res = gql(
        "mutation V($input: VolumeCreateInput!) { volumeCreate(input: $input) { id name } }",
        {"input": {"projectId": project_id, "environmentId": env_id, "serviceId": service_id, "mountPath": mount}},
    )
    return res["volumeCreate"]["id"]


def get_or_make_tcp_proxy(env_id: str, service_id: str, application_port: int = 51413) -> tuple[str, int, int]:
    """Return (domain, proxyPort, applicationPort), creating the proxy if absent."""
    res = gql(
        "query($eid: String!, $sid: String!) { tcpProxies(environmentId: $eid, serviceId: $sid) { id domain proxyPort applicationPort } }",
        {"eid": env_id, "sid": service_id},
    )["tcpProxies"]
    if res:
        p = res[0]
        return p["domain"].rstrip("."), p["proxyPort"], p["applicationPort"]
    res = gql(
        "mutation X($input: TCPProxyCreateInput!) { tcpProxyCreate(input: $input) { domain proxyPort applicationPort } }",
        {"input": {"environmentId": env_id, "serviceId": service_id, "applicationPort": application_port}},
    )["tcpProxyCreate"]
    return res["domain"].rstrip("."), res["proxyPort"], res["applicationPort"]


def get_or_make_domain(project_id: str, env_id: str, service_id: str) -> str:
    """Create a railway-managed *.up.railway.app domain for the service."""
    res = gql(
        "query($pid: String!, $eid: String!, $sid: String!) {"
        "  domains(projectId: $pid, environmentId: $eid, serviceId: $sid) {"
        "    serviceDomains { domain }"
        "  } }",
        {"pid": project_id, "eid": env_id, "sid": service_id},
    )["domains"]
    sds = res.get("serviceDomains", []) or []
    if sds:
        return sds[0]["domain"]
    res = gql(
        "mutation D($input: ServiceDomainCreateInput!) { serviceDomainCreate(input: $input) { domain } }",
        {"input": {"environmentId": env_id, "serviceId": service_id, "targetPort": 8080}},
    )
    return res["serviceDomainCreate"]["domain"]


def trigger_redeploy(env_id: str, service_id: str) -> str:
    """Force a deploy from the connected git source."""
    res = gql(
        "mutation S($sid: String!, $eid: String!) { serviceInstanceRedeploy(serviceId: $sid, environmentId: $eid) }",
        {"sid": service_id, "eid": env_id},
    )
    return json.dumps(res)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--env-id", required=True)
    ap.add_argument("--repo", default="audiodude/0bt")
    ap.add_argument("--branch", default="rewrite-2026")
    ap.add_argument("--mount", default="/data")
    args = ap.parse_args()

    rpc_pw_file = os.path.expanduser("~/.railway-0bt-transmission-pw")
    if os.path.exists(rpc_pw_file):
        rpc_pw = open(rpc_pw_file).read().strip()
    else:
        rpc_pw = secrets.token_hex(16)
        with open(rpc_pw_file, "w") as f:
            f.write(rpc_pw + "\n")
        os.chmod(rpc_pw_file, 0o600)

    initial_vars = {
        "TRANSMISSION_RPC_PASSWORD": rpc_pw,
        "FHOST_BASE_URL": "https://placeholder",  # patched after we know the domain
        "FHOST_TRACKERS": ",".join([
            "udp://tracker.opentrackr.org:1337/announce",
            "udp://tracker.openbittorrent.com:6969/announce",
            "udp://open.tracker.cl:1337/announce",
            "udp://exodus.desync.com:6969/announce",
        ]),
        "GUNICORN_WORKERS": "2",
        "GUNICORN_TIMEOUT": "600",
        "PORT": "8080",
        "BT_PEER_PORT": "51413",
    }

    print("→ ensuring service…")
    service_id = find_or_create_service(args.project_id, args.env_id, args.repo, args.branch, initial_vars)
    print(f"   service_id = {service_id}")

    print("→ ensuring volume…")
    volume_id = get_or_make_volume(args.project_id, args.env_id, service_id, args.mount)
    print(f"   volume_id  = {volume_id}")

    print("→ ensuring service domain…")
    domain = get_or_make_domain(args.project_id, args.env_id, service_id)
    print(f"   domain     = https://{domain}")

    print("→ patching FHOST_BASE_URL to the real domain…")
    upsert_var(args.project_id, args.env_id, service_id, "FHOST_BASE_URL", f"https://{domain}")

    print("→ ensuring TCP proxy for BT peer port…")
    bt_domain, bt_proxy_port, bt_app_port = get_or_make_tcp_proxy(args.env_id, service_id, application_port=51413)
    print(f"   bt: {bt_domain}:{bt_proxy_port} -> container:{bt_app_port}")

    print("→ wiring transmission ports to the proxy…")
    # Transmission's peer-port is both listen + announce. Set it to the public
    # proxyPort so announces are reachable. socat in bt-bridge.sh will forward
    # incoming traffic (which lands at applicationPort) to that listen port.
    upsert_var(args.project_id, args.env_id, service_id, "BT_PEER_PORT", str(bt_proxy_port))
    upsert_var(args.project_id, args.env_id, service_id, "BT_ANNOUNCE_PORT", str(bt_proxy_port))
    upsert_var(args.project_id, args.env_id, service_id, "BT_BRIDGE_FROM", str(bt_app_port))
    upsert_var(args.project_id, args.env_id, service_id, "BT_PUBLIC_HOST", bt_domain)
    upsert_var(args.project_id, args.env_id, service_id, "BT_PUBLIC_PORT", str(bt_proxy_port))

    print("→ triggering redeploy…")
    print(trigger_redeploy(args.env_id, service_id))

    print("done")
    print(f"PROJECT_ID={args.project_id}")
    print(f"ENV_ID={args.env_id}")
    print(f"SERVICE_ID={service_id}")
    print(f"VOLUME_ID={volume_id}")
    print(f"DOMAIN=https://{domain}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
