"""Offline web-client intelligence derived ONLY from saved public HTML/JS evidence.

Never executes web code. Raw JS and 512-character source slices are persisted so
every reported endpoint and feature claim can be checked against exact evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

MAX_FILES = 56
MAX_SOURCE_CHARS = 800_000
CHUNK = 512
ROUTE = re.compile(r"(?<![\w-])/(?:api|trpc|graphql)/[A-Za-z0-9_./{}?=&%:-]{2,125}")
FRAMEWORKS = {
    "Next.js": re.compile(r"/_next/static/|self\.__next_f|__NEXT_DATA__", re.I),
    "React": re.compile(r"react-dom|react\.createElement|__reactFiber", re.I),
    "Vue": re.compile(r"vue\.runtime|__VUE__", re.I),
    "Stripe": re.compile(r"js\.stripe\.com|stripe\.redirectToCheckout|Stripe\(", re.I),
    "PostHog": re.compile(r"posthog", re.I),
    "Microsoft Clarity": re.compile(r"clarity\.ms|clarity\(", re.I),
    "Google Analytics": re.compile(r"googletagmanager|google-analytics", re.I),
    "reCAPTCHA": re.compile(r"recaptcha|grecaptcha", re.I),
    "Cloudflare": re.compile(r"challenges\.cloudflare\.com|turnstile", re.I),
}
OTHER_MARKERS = {
    "text_input": re.compile(r"inputText|input_text|submittedText", re.I),
    "rewrite_style": re.compile(r"customStyleSamples|writingLevel|styleId|selectiveRephrase", re.I),
    "login_token": re.compile(r"accessToken|isLogged|refreshToken", re.I),
    "usage_limits": re.compile(r"wordLimit|trialNumber|limit_exceeded", re.I),
    "human_feedback": re.compile(r"feedback/quality|detector-report", re.I),
    "payment": re.compile(r"stripe|checkout|billingCycle", re.I),
}
CAPTURE_LIMITATIONS = [
    "Only unauthenticated, same-origin, publicly served HTML and JavaScript.",
    "No headless browser, JavaScript execution, or interaction with private APIs.",
    "Client endpoint references do not prove that endpoints are reachable or used at runtime.",
    "Technology mentions are heuristics and do not identify the private model provider.",
    "Snapshot lines in normalized/*.txt are synthetic 512-character spans of captured JavaScript.",
]


def _safe_path(root, name):
    if (not isinstance(name, str) or not name or len(name) > 120
            or name.startswith("/") or "\\" in name or ".." in name.split("/")):
        raise ValueError("invalid_capture_path")
    file = root / name
    if not file.resolve().is_relative_to(root.resolve()) or file.is_symlink():
        raise ValueError("invalid_capture_path")
    return file


def _source_preview(body, pos, radius=105):
    return body[max(0, pos - radius):pos + radius].replace("\r", " ").replace("\n", " ")[:230]


def analyze_and_save(job_root, capture):
    """Persist immutable public-code evidence, build bounded architecture report."""
    if (not isinstance(capture, dict) or capture.get("ok") is not True
            or not isinstance(capture.get("files"), list)
            or not 1 <= len(capture["files"]) <= MAX_FILES):
        raise ValueError("invalid_capture")
    root = job_root / "sources"
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    all_routes = {}
    technology_matches = {}
    feature_matches = {}
    assets = []
    total_bytes = 0
    for entry in capture["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "content", "url"}:
            raise ValueError("invalid_capture_entry")
        if not isinstance(entry["content"], str) or len(entry["content"]) > MAX_SOURCE_CHARS:
            raise ValueError("capture_entry_too_large")
        path = _safe_path(root, entry["path"])
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists():
            raise ValueError("duplicate_capture_path")
        encoded = entry["content"].encode("utf-8")
        total_bytes += len(encoded)
        if total_bytes > 13_000_000:
            raise ValueError("capture_too_large")
        path.write_bytes(encoded)
        digest = hashlib.sha256(encoded).hexdigest()
        assets.append({"file": entry["path"], "url": str(entry["url"])[:250],
                       "sha256": digest, "bytes": len(encoded)})
        # JavaScript is typically minified into one enormous line. Store
        # synthetic 512-character slices as indexable, line-citable evidence.
        if entry["path"].endswith(".js"):
            cleaned = entry["content"].replace("\r", " ").replace("\n", " ")
            evidence_name = "normalized/" + Path(entry["path"]).name + ".txt"
            evidence_file = _safe_path(root, evidence_name)
            evidence_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            evidence_file.write_text(
                "\n".join(cleaned[i:i+CHUNK] for i in range(0, len(cleaned), CHUNK)) + "\n",
                encoding="utf-8"
            )
            source = evidence_name
        else:
            cleaned = entry["content"]
            source = entry["path"]
        for match in ROUTE.finditer(cleaned):
            name = match.group(0).rstrip(".,;:)}]'\"")
            # Ignore blank or resource-like routes; endpoints are static references.
            if not name or len(name) < 5:
                continue
            line = match.start() // CHUNK + 1 if source.startswith("normalized/") else cleaned.count("\n", 0, match.start()) + 1
            evidence = {"endpoint": name, "file": source, "line": line,
                        "evidence": f"{source}:{line}", "excerpt": _source_preview(cleaned, match.start())}
            if name not in all_routes:
                all_routes[name] = evidence
        for name, regex in FRAMEWORKS.items():
            match = regex.search(cleaned)
            if match and name not in technology_matches:
                line = match.start() // CHUNK + 1 if source.startswith("normalized/") else cleaned.count("\n", 0, match.start()) + 1
                technology_matches[name] = {"name": name, "file": source, "line": line, "evidence": f"{source}:{line}"}
        for name, regex in OTHER_MARKERS.items():
            match = regex.search(cleaned)
            if match and name not in feature_matches:
                line = match.start() // CHUNK + 1 if source.startswith("normalized/") else cleaned.count("\n", 0, match.start()) + 1
                feature_matches[name] = {"signal": name, "evidence": f"{source}:{line}",
                                         "excerpt": _source_preview(cleaned, match.start())}
    return {
        "snapshot_epoch": int(time.time()),
        "entry_url": str(capture.get("url", ""))[:400],
        "files_captured": len(assets),
        "captured_bytes": total_bytes,
        "client_assets": assets[:55],
        "api_endpoints": sorted(all_routes.values(), key=lambda x: x["endpoint"])[:100],
        "api_endpoint_total": len(all_routes),
        "technology_indicators": list(technology_matches.values()),
        "client_feature_signals": list(feature_matches.values()),
        "asset_errors": capture.get("asset_errors", [])[:30],
        "limitations": CAPTURE_LIMITATIONS,
        "untrusted_target_data": True,
    }


def compact_web_report(report, offset=0, limit=12):
    if type(offset) is not int or type(limit) is not int or not 0 <= offset <= 150_000 or not 1 <= limit <= 25:
        raise ValueError("invalid_pagination")
    obs = report.get("observations", {})
    rows = obs.get("api_endpoints", [])
    return {
        "ok": True, "artifact": report.get("artifact"), "mode": "web",
        "analysis_result": "partial" if obs.get("asset_errors") else "clean_static_snapshot",
        "files_captured": obs.get("files_captured", 0),
        "captured_bytes": obs.get("captured_bytes", 0),
        "technologies": obs.get("technology_indicators", []),
        "feature_signals": obs.get("client_feature_signals", []),
        "api_endpoint_total": obs.get("api_endpoint_total", 0),
        "offset": offset, "limit": limit, "total": len(rows),
        "api_endpoints": rows[offset:offset+limit],
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "asset_errors": obs.get("asset_errors", []),
        "limitations": CAPTURE_LIMITATIONS,
        "untrusted_target_data": True,
    }


def web_graph(report, query):
    if not isinstance(query, str) or not 2 <= len(query) <= 240 or any(ord(c) < 32 for c in query):
        raise ValueError("invalid_query")
    obs = report.get("observations", {})
    matches = [x for x in obs.get("api_endpoints", [])
               if query.casefold() in x["endpoint"].casefold() or query.casefold() in x["file"].casefold()][:40]
    return {"ok": True, "graph_type": "static_client_api_references_not_verified_calls",
            "nodes": sorted({v for m in matches for v in (m["file"], m["endpoint"])}),
            "edges": [{"source_file": m["file"], "target_symbol": m["endpoint"],
                       "relation": "client_route_reference", "line": m["line"],
                       "evidence": m["evidence"]} for m in matches],
            "truncated": len(matches) >= 40, "untrusted_target_data": True}


def compare_web_reports(left, right):
    if left.get("mode") != "web" or right.get("mode") != "web":
        raise ValueError("web_jobs_required")
    a, b = left["observations"], right["observations"]
    routes_a = {x["endpoint"] for x in a.get("api_endpoints", [])}
    routes_b = {x["endpoint"] for x in b.get("api_endpoints", [])}
    technologies_a = {x["name"] for x in a.get("technology_indicators", [])}
    technologies_b = {x["name"] for x in b.get("technology_indicators", [])}
    return {
        "ok": True,
        "older_url": a.get("entry_url"),
        "newer_url": b.get("entry_url"),
        "new_routes": sorted(routes_b - routes_a)[:100],
        "removed_routes": sorted(routes_a - routes_b)[:100],
        "new_technologies": sorted(technologies_b - technologies_a),
        "removed_technologies": sorted(technologies_a - technologies_b),
        "older_files": a.get("files_captured"),
        "newer_files": b.get("files_captured"),
        "limitations": ["Code references only; routes may be dead code or conditional.",
                        "Server behavior, deployments and model providers remain unknown.",
                        "Site changes or CDN/cache variants may affect snapshots."],
        "untrusted_target_data": True,
    }
