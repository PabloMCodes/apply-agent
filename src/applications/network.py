"""Keep employer pages from reaching local/private services through the browser."""

from functools import lru_cache
import ipaddress
import socket
from urllib.parse import urlparse


@lru_cache(maxsize=512)
def public_host(host):
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (ValueError, OSError):
        return False


def public_request(url):
    try:
        parsed = urlparse(url)
        return (parsed.scheme == 'https' and parsed.port in (None, 443) and not parsed.username
                and bool(parsed.hostname) and public_host(parsed.hostname))
    except ValueError:
        return False
