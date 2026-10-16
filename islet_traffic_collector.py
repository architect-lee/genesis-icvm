#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# <Identity>
# 
# > Identity: islet_traffic_collector.py
# 
# </Identity>

"""
Genesis IsletArchive 24/365 Traffic Telemetry Collector (v1.0)
- Target 1: Zenodo Records 22959347 (ICVM) & 23351868 (Attention Dynamics)
- Target 2: GitHub Traffic API for 'architect-lee/genesis-icvm'
- Target 3: Cloud Firestore 'traffic_snapshots' Collection (Spark 100% Free Plan)
- Design: Zero External Dependencies (Standard Library + Built-in SSL)
"""

import os
import sys
import json
import time
import datetime
import urllib.request
import urllib.parse
import ssl
import base64
import subprocess
import tempfile
from typing import Dict, Any, Optional, Tuple

DEFAULT_KEY_PATH = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY_PATH", "/Users/sprout/.gemini/antigravity/isletarchive-service-account.json")
DEFAULT_GH_PAT = os.environ.get("GH_PAT", "")
FIRESTORE_PROJECT_ID = "isletarchive"
ZENODO_RECORDS = {
    "22959347": "Genesis In-Context Virtual Machine (ICVM)",
    "23351868": "Deterministic Quantum Mechanics of Transformer In-Context Attention"
}
GITHUB_REPO = "architect-lee/genesis-icvm"


def get_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def fetch_zenodo_record(record_id: str, ssl_ctx: ssl.SSLContext) -> Dict[str, Any]:
    url = f"https://zenodo.org/api/records/{record_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    req = urllib.request.Request(url, headers=headers)
    data = None
    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e_urllib:
        # Robust fallback via curl (immune to Cloudflare TLS fingerprint blocks)
        try:
            res = subprocess.run(
                ["curl", "-s", "--max-time", "15", url],
                capture_output=True, text=True
            )
            if res.returncode == 0 and res.stdout.strip().startswith("{"):
                data = json.loads(res.stdout)
            else:
                raise RuntimeError(f"urllib failed ({e_urllib}) and curl failed: {res.stderr or res.stdout[:100]}")
        except Exception as e_curl:
            print(f"[WARN] Failed to fetch Zenodo {record_id}: {e_curl}", file=sys.stderr)
            return {
                "record_id": record_id,
                "title": ZENODO_RECORDS.get(record_id, ""),
                "views": 0, "unique_views": 0, "downloads": 0, "unique_downloads": 0,
                "status": f"ERROR: {str(e_curl)}"
            }

    stats = data.get("stats", {})
    metadata = data.get("metadata", {})
    files_raw = data.get("files", [])
    files = [{
        "key": f.get("key", ""),
        "size": f.get("size", 0),
        "checksum": f.get("checksum", "")
    } for f in files_raw]

    return {
        "record_id": record_id,
        "title": metadata.get("title", ZENODO_RECORDS.get(record_id, "")),
        "doi": data.get("doi", f"10.5281/zenodo.{record_id}"),
        "views": stats.get("views", 0),
        "unique_views": stats.get("unique_views", 0),
        "downloads": stats.get("downloads", 0),
        "unique_downloads": stats.get("unique_downloads", 0),
        "version_views": stats.get("version_views", 0),
        "version_unique_views": stats.get("version_unique_views", 0),
        "version_downloads": stats.get("version_downloads", 0),
        "version_unique_downloads": stats.get("version_unique_downloads", 0),
        "files_count": len(files),
        "files": files,
        "status": "OK"
    }


def fetch_github_traffic(repo: str, pat: str, ssl_ctx: ssl.SSLContext) -> Dict[str, Any]:
    headers = {
        "Authorization": f"token {pat}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
    }
    endpoints = {
        "views": f"https://api.github.com/repos/{repo}/traffic/views",
        "clones": f"https://api.github.com/repos/{repo}/traffic/clones",
        "referrers": f"https://api.github.com/repos/{repo}/traffic/popular/referrers",
        "paths": f"https://api.github.com/repos/{repo}/traffic/popular/paths"
    }
    results: Dict[str, Any] = {"repo": repo, "status": "OK"}
    for key, url in endpoints.items():
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=15) as resp:
                results[key] = json.loads(resp.read().decode())
        except Exception as e:
            print(f"[WARN] Failed to fetch GitHub traffic {key}: {e}", file=sys.stderr)
            results[key] = {"status": f"ERROR: {str(e)}"}

    # Fetch repository reputation and social metadata
    try:
        repo_req = urllib.request.Request(f"https://api.github.com/repos/{repo}", headers=headers)
        with urllib.request.urlopen(repo_req, context=ssl_ctx, timeout=15) as resp:
            meta = json.loads(resp.read().decode())
            results["metadata"] = {
                "stargazers_count": meta.get("stargazers_count", 0),
                "forks_count": meta.get("forks_count", 0),
                "watchers_count": meta.get("watchers_count", 0),
                "subscribers_count": meta.get("subscribers_count", 0),
                "open_issues_count": meta.get("open_issues_count", 0),
                "size_kb": meta.get("size", 0),
                "pushed_at": meta.get("pushed_at", ""),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", "")
            }
    except Exception as e:
        print(f"[WARN] Failed to fetch GitHub repo metadata: {e}", file=sys.stderr)
        results["metadata"] = {"status": f"ERROR: {str(e)}"}

    return results


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def get_firestore_access_token(key_path: str, ssl_ctx: ssl.SSLContext) -> Tuple[str, str]:
    if not os.path.exists(key_path):
        raw_key = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")
        if raw_key:
            sa = json.loads(raw_key)
        else:
            raise FileNotFoundError(f"Service account key not found at {key_path} or in env")
    else:
        with open(key_path, "r", encoding="utf-8") as f:
            sa = json.load(f)

    project_id = sa.get("project_id", FIRESTORE_PROJECT_ID)
    client_email = sa["client_email"]
    private_key = sa["private_key"]
    token_uri = sa.get("token_uri", "https://oauth2.googleapis.com/token")

    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    now = int(time.time())
    claims = {
        "iss": client_email,
        "scope": "https://www.googleapis.com/auth/datastore",
        "aud": token_uri,
        "exp": now + 3600,
        "iat": now
    }
    claim_set = b64url(json.dumps(claims).encode())
    sign_input = f"{header}.{claim_set}".encode()

    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write(private_key)
        tmp_key_file = tf.name

    try:
        proc = subprocess.Popen(
            ["openssl", "dgst", "-sha256", "-sign", tmp_key_file],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        sig, err = proc.communicate(sign_input)
        if proc.returncode != 0:
            raise RuntimeError(f"OpenSSL signing error: {err.decode()}")
    finally:
        if os.path.exists(tmp_key_file):
            os.unlink(tmp_key_file)

    jwt_token = f"{header}.{claim_set}.{b64url(sig)}"
    payload = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt_token
    }).encode()

    req = urllib.request.Request(
        token_uri, data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, context=ssl_ctx, timeout=15) as resp:
        token_data = json.loads(resp.read().decode())
        return token_data["access_token"], project_id


def python_to_firestore_value(val: Any) -> Dict[str, Any]:
    if val is None:
        return {"nullValue": None}
    elif isinstance(val, bool):
        return {"booleanValue": val}
    elif isinstance(val, int):
        return {"integerValue": str(val)}
    elif isinstance(val, float):
        return {"doubleValue": val}
    elif isinstance(val, str):
        return {"stringValue": val}
    elif isinstance(val, dict):
        return {"mapValue": {"fields": {k: python_to_firestore_value(v) for k, v in val.items()}}}
    elif isinstance(val, (list, tuple)):
        return {"arrayValue": {"values": [python_to_firestore_value(v) for v in val]}}
    else:
        return {"stringValue": str(val)}


def push_to_firestore(doc_id: str, data: Dict[str, Any], token: str, project_id: str, ssl_ctx: ssl.SSLContext) -> Dict[str, Any]:
    url = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents/traffic_snapshots?documentId={doc_id}"
    body = {"fields": {k: python_to_firestore_value(v) for k, v in data.items()}}
    encoded = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=encoded,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, context=ssl_ctx, timeout=20) as resp:
        return json.loads(resp.read().decode())


def collect_traffic_snapshot(key_path: str = DEFAULT_KEY_PATH, pat: str = DEFAULT_GH_PAT, dry_run: bool = False) -> Dict[str, Any]:
    ssl_ctx = get_ssl_context()
    now_kst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    iso_timestamp = now_kst.isoformat()
    doc_id = now_kst.strftime("%Y-%m-%dT%H%M%S_KST")

    print(f"[*] Starting IsletArchive traffic collection at {iso_timestamp} KST...")
    zenodo_results = {}
    for rec_id in ZENODO_RECORDS:
        print(f"    - Fetching Zenodo record {rec_id}...")
        zenodo_results[rec_id] = fetch_zenodo_record(rec_id, ssl_ctx)

    print(f"    - Fetching GitHub traffic for {GITHUB_REPO}...")
    github_results = fetch_github_traffic(GITHUB_REPO, pat, ssl_ctx)

    gh_views = github_results.get("views", {})
    gh_clones = github_results.get("clones", {})
    gh_meta = github_results.get("metadata", {})
    summary = {
        "total_zenodo_views": sum(r.get("views", 0) for r in zenodo_results.values()),
        "total_zenodo_unique_views": sum(r.get("unique_views", 0) for r in zenodo_results.values()),
        "total_zenodo_downloads": sum(r.get("downloads", 0) for r in zenodo_results.values()),
        "total_zenodo_unique_downloads": sum(r.get("unique_downloads", 0) for r in zenodo_results.values()),
        "github_views_14d": gh_views.get("count", 0),
        "github_uniques_14d": gh_views.get("uniques", 0),
        "github_clones_14d": gh_clones.get("count", 0),
        "github_unique_cloners_14d": gh_clones.get("uniques", 0),
        "github_stars": gh_meta.get("stargazers_count", 0),
        "github_forks": gh_meta.get("forks_count", 0),
        "github_subscribers": gh_meta.get("subscribers_count", 0),
        "github_open_issues": gh_meta.get("open_issues_count", 0),
    }

    snapshot = {
        "collected_at": iso_timestamp,
        "doc_id": doc_id,
        "collector_version": "v1.1_enriched",
        "zenodo": zenodo_results,
        "github": github_results,
        "summary": summary
    }

    # Save local cache
    cache_path = os.path.join(os.path.dirname(__file__), "traffic_cache.json")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print(f"[+] Local cache saved to: {cache_path}")

    if not dry_run:
        print("    - Obtaining Firestore OAuth2 token and pushing snapshot...")
        token, project_id = get_firestore_access_token(key_path, ssl_ctx)
        resp = push_to_firestore(doc_id, snapshot, token, project_id, ssl_ctx)
        print(f"[+] Firestore document created: {resp.get('name')}")
        snapshot["firestore_doc_name"] = resp.get("name")
    else:
        print("[!] Dry-run enabled: Firestore write skipped.")

    return snapshot


if __name__ == "__main__":
    key = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY_PATH", DEFAULT_KEY_PATH)
    pat = os.environ.get("GH_PAT", DEFAULT_GH_PAT)
    if not pat:
        _local_pat = os.path.expanduser("~/.gemini/antigravity/github_pat.txt")
        if os.path.exists(_local_pat):
            with open(_local_pat, "r", encoding="utf-8") as _f:
                pat = _f.read().strip()
    dry = "--dry-run" in sys.argv
    res = collect_traffic_snapshot(key_path=key, pat=pat, dry_run=dry)
    s = res["summary"]
    p1 = res["zenodo"]["22959347"]
    p2 = res["zenodo"]["23351868"]
    print("\n" + "=" * 70)
    print(" [ISLET_TRAFFIC_COLLECTOR v1.1] Enriched T0 Telemetry Summary")
    print("=" * 70)
    print(f" Collected At: {res['collected_at']}")
    print(f" Zenodo Total Views: {s['total_zenodo_views']} (Paper 1: {p1['views']}, Paper 2: {p2['views']})")
    print(f" Zenodo Unique Views: {s['total_zenodo_unique_views']} (Paper 1: {p1['unique_views']}, Paper 2: {p2['unique_views']})")
    print(f" Zenodo Total Downloads: {s['total_zenodo_downloads']} (Paper 1: {p1['downloads']}, Paper 2: {p2['downloads']})")
    print(f" Zenodo Unique Downloads: {s['total_zenodo_unique_downloads']} (Paper 1: {p1['unique_downloads']}, Paper 2: {p2['unique_downloads']})")
    print(f" GitHub Views (14d): {s['github_views_14d']} (Uniques: {s['github_uniques_14d']})")
    print(f" GitHub Clones (14d): {s['github_clones_14d']} (Unique Cloners: {s['github_unique_cloners_14d']})")
    print(f" GitHub Stars / Forks / Subs: {s['github_stars']} / {s['github_forks']} / {s['github_subscribers']}")
    print(f" Status: {'DRY RUN' if dry else 'COMMITTED TO FIRESTORE'}")
    print("=" * 70)
