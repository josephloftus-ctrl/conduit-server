"""URL validation — block requests to private/loopback/link-local networks.

Prevents SSRF attacks where the LLM could use web_fetch to hit internal
services (e.g. localhost:8080/api/settings/tools to self-escalate permissions).
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

log = logging.getLogger("conduit.tools.url_validation")

BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),
]

BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain"}


def is_url_blocked(url: str) -> str | None:
    """Check if a URL targets a private/internal network.

    Returns an error message string if blocked, None if the URL is safe.
    Fails closed: if DNS resolution fails, the URL is blocked.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL"

    hostname = parsed.hostname
    if not hostname:
        return "URL has no hostname"

    # Block known localhost aliases
    if hostname.lower() in BLOCKED_HOSTNAMES:
        return f"Blocked: {hostname} is a loopback address"

    # Resolve hostname to IP and check against blocked ranges
    try:
        resolved = socket.gethostbyname(hostname)
    except socket.gaierror:
        return f"Blocked: could not resolve hostname {hostname}"

    try:
        addr = ipaddress.ip_address(resolved)
    except ValueError:
        return f"Blocked: invalid resolved address {resolved}"

    for network in BLOCKED_NETWORKS:
        if addr in network:
            log.warning("SSRF blocked: %s resolved to %s (in %s)", url, resolved, network)
            return f"Blocked: {hostname} resolves to private address {resolved}"

    return None
