"""URL and address judgement for fetching customer media (pure; adloop's image_fetch pattern).

Only https, no credentials in the URL, default or explicit port, and every resolved address
must be public (IPv6 forms that embed IPv4 are judged by the embedded address).
"""

from dataclasses import dataclass
import ipaddress
from urllib.parse import urlsplit

from ads_mcp.core.result import Err, Ok, Result

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
MAX_URL = 2048


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    path: str  # path + query, as sent


def parse_https_url(url: str) -> Result[Target, str]:
    if len(url) > MAX_URL:
        return Err("The URL is too long.")
    try:
        parts = urlsplit(url.strip())
        port = parts.port or 443
    except ValueError:
        return Err("The URL cannot be parsed.")
    if parts.scheme.lower() != "https":
        return Err("Only https:// URLs are fetched.")
    if parts.username is not None or parts.password is not None:
        return Err("The URL carries credentials; refused.")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        return Err("The URL has no host.")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return Err("The URL's host is not a valid name.")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return Ok(Target(host, port, path))


def non_public_reason(address: str) -> str | None:
    """Why this address must not be fetched from, or None when it is public."""
    try:
        addr = ipaddress.ip_address(address)
    except ValueError:
        return "not an IP address"
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return non_public_reason(str(addr.ipv4_mapped))
        embedded = [addr.sixtofour, *(addr.teredo or ())]
        if addr in _NAT64:
            embedded.append(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
        bad = [
            e for e in embedded if e is not None and non_public_reason(str(e))
        ]
        if bad:
            return f"embeds non-public {bad[0]}"
    flags = (
        "unspecified",
        "loopback",
        "link_local",
        "multicast",
        "private",
        "reserved",
    )
    hit = next((f for f in flags if getattr(addr, f"is_{f}")), None)
    if hit:
        return hit.replace("_", "-")
    return None if addr.is_global else "not globally routable"


def judge_addresses(host: str, addresses: tuple[str, ...]) -> Result[str, str]:
    """The address to dial: refuse when there is none or when ANY is non-public (no picking)."""
    if not addresses:
        return Err(f"{host} does not resolve.")
    bad = [(a, non_public_reason(a)) for a in addresses]
    refused = [(a, why) for a, why in bad if why]
    if refused:
        a, why = refused[0]
        return Err(
            f"{host} resolves to a non-public address ({a}, {why}); refused."
        )
    return Ok(addresses[0])
