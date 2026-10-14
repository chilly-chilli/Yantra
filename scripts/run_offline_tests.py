"""Run deterministic tests with remote transports blocked; localhost is permitted."""
from contextlib import ExitStack
import ipaddress
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1] / "tests")]


def local(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main():
    attempts = []

    def guarded(original, host_of):
        def call(*args, **kwargs):
            host = host_of(args, kwargs)
            if not local(host):
                attempts.append(str(host))
                raise AssertionError("Unmocked external transport is forbidden in offline tests")
            return original(*args, **kwargs)
        return call

    import httpx
    import primp
    with ExitStack() as stack:
        stack.enter_context(patch.object(socket, "getaddrinfo", guarded(socket.getaddrinfo, lambda a, k: a[0])))
        stack.enter_context(patch.object(socket.socket, "connect", guarded(socket.socket.connect, lambda a, k: a[1][0])))
        stack.enter_context(patch.object(httpx.HTTPTransport, "handle_request", guarded(httpx.HTTPTransport.handle_request, lambda a, k: a[1].url.host)))
        for method in ("get", "post"):
            stack.enter_context(patch.object(primp.Client, method, guarded(getattr(primp.Client, method), lambda a, k: urlsplit(a[1]).hostname)))
        suite = unittest.defaultTestLoader.discover("tests", pattern=sys.argv[1] if len(sys.argv) > 1 else "test_*.py")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    if attempts:
        print(f"Offline boundary rejected {len(attempts)} unexpected external calls.", file=sys.stderr)
    return 0 if result.wasSuccessful() and not attempts else 1


if __name__ == "__main__":
    raise SystemExit(main())
