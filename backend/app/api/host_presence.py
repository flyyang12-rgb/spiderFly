"""Short-lived LAN hints for display in the admin UI, never for authorization."""
from ipaddress import IPv6Address, ip_address, ip_network
from threading import Lock
from time import monotonic

from fastapi import Request

from ..services.host_dispatch import HEARTBEAT_TIMEOUT


_lan = tuple(map(ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")))
_recent = {}
_lock = Lock()


def _address(value):
    try:
        addr = ip_address(value)
    except ValueError:
        return None
    return (addr.ipv4_mapped or addr) if isinstance(addr, IPv6Address) else addr


def _peer(request: Request):
    if not request.client or any(key in request.headers for key in ("forwarded", "via", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip")):
        return None
    addr = _address(request.client.host)
    if addr and (addr.is_loopback or addr.is_link_local or any(addr in net for net in _lan)):
        return addr
    return None


def heartbeat(request: Request, host_id: int):
    addr = _peer(request)
    now = monotonic()
    with _lock:
        for key in [key for key, (_, expiry) in _recent.items() if expiry <= now]:
            del _recent[key]
        if addr is None:
            _recent.pop(host_id, None)
        else:
            _recent[host_id] = (addr, now + HEARTBEAT_TIMEOUT)


def viewer(request: Request, hosts: list[dict]) -> dict:
    addr = _peer(request)
    if addr is None:
        return {"kind": "unknown"}
    now = monotonic()
    with _lock:
        matches = [key for key, (peer, expiry) in _recent.items() if peer == addr and expiry > now]
    server = request.scope.get("server")
    controller = addr.is_loopback or (server and addr == _address(server[0]))
    if len(matches) > 1 or (controller and matches):
        return {"kind": "shared"}
    if controller:
        return {"kind": "controller"}
    if matches and any(h["id"] == matches[0] and h["online"] and h["approval_status"] in ("pending", "approved") for h in hosts):
        return {"kind": "agent", "host_id": matches[0]}
    return {"kind": "unknown"}
