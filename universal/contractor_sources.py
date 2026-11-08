"""Opt-in, tenant-scoped read-only HTTPS JSON contractor source adapter.

Customer integrations require an authorized server-side connector configuration.
No API tokens or source URLs are accepted from normal browser requests.
No Terraboost or third-party credentials are embedded in source control.

Example DCC_CONTRACTOR_CONNECTORS_JSON (set on SERVER only):
{
  "personal": {
    "my-crm": {
      "url": "https://api.customer-owned-system.com/v1/contractors",
      "token_env": "DCC_CONNECTOR_MY_CRM_TOKEN",
      "records_path": "data.contractors",
      "mapping": {
        "external_id": "id", "name": "displayName", "email": "email",
        "status": "status", "home_address": "address.full",
        "latitude": "location.lat", "longitude": "location.lon",
        "service_radius_miles": "coverage_miles"
      },
      "status_map": {"approved": "active", "pending": "in_training",
                     "disabled": "inactive"}
    }
  }
}
"""
import ipaddress
import json
import os
import re
import socket
from urllib.parse import urlparse

import httpx

SOURCE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
TOKEN_ENV_RE = re.compile(r"^DCC_CONNECTOR_[A-Z0-9_]{1,80}_TOKEN$")
MAX_API_RECORDS = 2000
MAX_RESPONSE_BYTES = 2_000_000

def source_key(source):
    source = str(source).strip().lower()
    if not SOURCE_RE.fullmatch(source) or source == "demo":
        raise ValueError("Source key must be 2-32 lowercase letters/numbers, dashes or underscores; 'demo' is reserved")
    return source

def _all_config():
    raw = os.getenv("DCC_CONTRACTOR_CONNECTORS_JSON", "")
    if not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError as exc:
        raise ValueError("Invalid JSON connector configuration") from exc
    if not isinstance(loaded, dict):
        raise ValueError("Connector configuration must be an organization map")
    return loaded

def configured_sources(organization):
    sources = _all_config().get(organization, {})
    if not isinstance(sources, dict):
        raise ValueError("Configured organization sources must be an object")
    return {source_key(k): v for k, v in sources.items()}

def source_descriptions(organization):
    """Safe metadata only — never return URL, auth environment names or tokens."""
    return [{"source":name,"kind":"https_json", "ready":isinstance(config, dict)}
            for name, config in sorted(configured_sources(organization).items())]

def _trusted_url(value):
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError("Connector URL is missing or too long")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.port not in (None, 443) or not parsed.hostname:
        raise ValueError("Connector must use HTTPS on port 443")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Connector URL cannot embed credentials, query or fragments")
    hostname = parsed.hostname.rstrip(".").lower()
    if (hostname in ("localhost",) or hostname.endswith((".localhost", ".local", ".internal"))
            or hostname.endswith(".test")):
        raise ValueError("Local/private connector host is not allowed")
    try:
        ipaddress.ip_address(hostname)
        raise ValueError("Connector host must be a public DNS hostname, not an IP")
    except ValueError as exc:
        if "must be a public DNS" in str(exc):
            raise
    return value, hostname

def _require_public_dns(hostname):
    """Defense in depth; configured sources should be independently trusted too."""
    try:
        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Configured connector hostname could not be resolved") from exc
    if not infos:
        raise ValueError("Configured connector hostname could not be resolved")
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ValueError("Configured connector resolves to a non-public IP")
    # IMPORTANT: DNS checks are not strict pinning. Only admins with access to
    # deployment secrets may configure sources; never accept user-supplied URLs.

def dotted(record, field, default=None):
    if not isinstance(field, str) or not field:
        return default
    value = record
    for key in field.split("."):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value

def translate_records(response_json, config):
    path = config.get("records_path", "contractors")
    raw = dotted(response_json, path) if path else response_json
    if not isinstance(raw, list):
        raise ValueError("Configured records_path did not resolve to a list")
    if len(raw) > MAX_API_RECORDS:
        raise ValueError("Too many contractors in one API response; pagination is not yet supported")
    mapping = config.get("mapping") or {}
    if not isinstance(mapping, dict):
        raise ValueError("Connector mapping must be an object")
    statuses = config.get("status_map", {})
    if not isinstance(statuses, dict):
        raise ValueError("Connector status_map must be an object")
    result = []
    for i, entry in enumerate(raw, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"API record {i} must be an object")
        def get(field, default=None):
            return dotted(entry, mapping.get(field, field), default)
        status = str(get("status", "inactive") or "inactive").lower().strip()
        status = statuses.get(status, status).replace(" ", "_")
        if status not in ("active", "in_training", "inactive"):
            raise ValueError(f"Unmapped status at API record {i}: {status[:24]}")
        try:
            latitude = float(get("latitude"))
            longitude = float(get("longitude"))
            radius = float(get("service_radius_miles", 100) or 100)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Missing/invalid coordinates or service radius at API record {i}") from exc
        result.append({
            "external_id": str(get("external_id", "") or ""),
            "name": str(get("name", "") or ""),
            "email": get("email") or None,
            "status": status,
            "home": {"lat": latitude, "lon": longitude},
            "service_radius_miles": radius,
            "home_address": str(get("home_address", "") or "")[:300] or None,
        })
    return result

def fetch_contractors(organization, source):
    source = source_key(source)
    config = configured_sources(organization).get(source)
    if not isinstance(config, dict):
        raise ValueError("This company has no authorized connector with that key")
    url, hostname = _trusted_url(config.get("url"))
    _require_public_dns(hostname)
    env_name = config.get("token_env", "")
    if not isinstance(env_name, str) or not TOKEN_ENV_RE.fullmatch(env_name):
        raise ValueError("Connector token_env must reference a dedicated DCC_CONNECTOR_*_TOKEN variable")
    token = os.getenv(env_name, "")
    if not token:
        raise ValueError("Connector token has not been configured on this server")
    # Never log the bearer token, response body, or any employer/client data.
    with httpx.Client(follow_redirects=False, trust_env=False,
                      timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        with client.stream("GET", url,
                           headers={"Authorization": f"Bearer {token}",
                                    "Accept": "application/json"}) as response:
            response.raise_for_status()
            if response.is_redirect:
                raise ValueError("Connector redirects are disabled")
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise ValueError("Connector response exceeds 2 MB safety limit")
    try:
        parsed = json.loads(content.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Connector returned invalid JSON") from exc
    return translate_records(parsed, config)
