"""Where a webhook may go, and how it is sent there (M48).

A webhook is a request to an address that a project's owner wrote, made by the server, so it is the
one place where Vrum can be tricked into reaching something it should not (a service on the same
machine, a private network, a cloud provider's metadata address). So:

- the address must be `https` (and `http` only when the person who runs the server allows it for a
  receiver on this machine, `VRUM_WEBHOOK_ALLOW_INSECURE_HTTP`), with a host and no user name or
  password in it, at most 500 characters;
- the name is resolved and EVERY address it gives must be a public one: loopback, private,
  link-local (which includes the metadata address 169.254.169.254), shared, multicast, reserved and
  unspecified addresses are refused, and so is an IPv6 address that wraps one of those. This is
  checked when the webhook is saved and again just before each call;
- the connection goes to the very address that was checked (the name is not looked up a second
  time, so a name that changes its answer between the check and the call gets nothing), with the
  name still used for the certificate and the Host header;
- nothing follows a redirect: any 3xx is a failure;
- a person who runs the server may allow names, addresses or networks (`VRUM_WEBHOOK_ALLOWED_HOSTS`,
  such as `10.0.0.0/8`) because their own network is theirs to open.

Nothing here logs or shows an address: webhook addresses often hold a secret token."""

import http.client
import ipaddress
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from vrum.config import WebhookSettings

MOST_URL_CHARS = 500
_TIMEOUT_SECONDS = 10.0
_MOST_ANSWER_BYTES = 2048

Resolver = Callable[[str, int], list[str]]


class AddressRefused(Exception):
    """An address a webhook may not go to. The words say why and never repeat the address."""


class DeliveryFailed(Exception):
    """A call that did not get a good answer. The words say what happened, never the address."""

    def __init__(self, words: str, status: int | None = None) -> None:
        super().__init__(words)
        self.words = words
        self.status = status


@dataclass(frozen=True)
class Target:
    tls: bool
    host: str
    port: int
    path: str


def parse(url: str, settings: WebhookSettings) -> Target:
    """The parts of an address, or a refusal saying what is wrong with it."""
    text = url.strip()
    if not text or len(text) > MOST_URL_CHARS:
        raise AddressRefused(
            f"The address has to be written out, at most {MOST_URL_CHARS} characters."
        )
    if any(ord(char) < 33 or ord(char) == 127 for char in text):
        raise AddressRefused("The address may not hold spaces or control characters.")
    try:
        parts = urlsplit(text)
        port = parts.port
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        raise AddressRefused("That is not a web address.") from None
    if parts.scheme not in ("https", "http"):
        raise AddressRefused("The address has to start with https://.")
    if parts.scheme == "http" and not settings.allow_insecure_http:
        raise AddressRefused(
            "The address has to start with https://. A webhook is not sent without encryption."
        )
    if not host:
        raise AddressRefused("The address needs a host name, such as hooks.example.com.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise AddressRefused("The address may not hold a user name or a password.")
    if parts.fragment:
        raise AddressRefused("The address may not end with a #fragment.")
    if port == 0:
        raise AddressRefused("The port is not a usable one.")
    tls = parts.scheme == "https"
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return Target(tls=tls, host=host, port=port or (443 if tls else 80), path=path)


def _networks(settings: WebhookSettings) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    found: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in settings.allowed_hosts:
        try:
            found.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return found


def why_not(address: str) -> str | None:
    """Why this address is not a public one, in words, or None when it is."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return "is not an address"
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "is a loopback address (this machine)"
    if ip.is_link_local:
        return "is a link-local address (such as a cloud provider's metadata address)"
    if ip.is_private:
        return "is on a private network"
    if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return "is not a public address"
    if not ip.is_global:
        return "is not a public address"
    return None


def _permitted(host: str, address: str, settings: WebhookSettings) -> bool:
    """Whether the person who runs the server has opened this name or address."""
    if host in settings.allowed_hosts:
        return True
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return any(ip in network for network in _networks(settings))


def lookup(host: str, port: int) -> list[str]:
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    return list(dict.fromkeys(str(info[4][0]) for info in found))


def public_addresses(
    target: Target, settings: WebhookSettings, resolver: Resolver = lookup
) -> list[str]:
    """Every address the name gives, all of them checked, or a refusal. An address the person who
    runs the server has opened is let through."""
    literal = True
    try:
        ipaddress.ip_address(target.host.strip("[]"))
    except ValueError:
        literal = False
    addresses = [target.host.strip("[]")] if literal else resolver(target.host, target.port)
    if not addresses:
        raise AddressRefused("That host name does not resolve to an address.")
    for address in addresses:
        reason = why_not(address)
        if reason is not None and not _permitted(target.host, address, settings):
            raise AddressRefused(
                f"That address {reason}, and a webhook may only go to a public address. Whoever "
                "runs this server can allow it with VRUM_WEBHOOK_ALLOWED_HOSTS."
            )
    return addresses


class _Pinned(http.client.HTTPConnection):
    """A connection that goes to one address and no other, with the host name kept for the Host
    header and, over TLS, for the certificate."""

    def __init__(self, target: Target, address: str, timeout: float) -> None:
        super().__init__(target.host, target.port, timeout=timeout)
        self._address = address
        self._target = target
        self._context = ssl.create_default_context()

    def connect(self) -> None:
        plain = socket.create_connection((self._address, self.port), self.timeout)
        if self._target.tls:
            self.sock = self._context.wrap_socket(plain, server_hostname=self._target.host)
        else:
            self.sock = plain


def post(
    target: Target,
    settings: WebhookSettings,
    headers: dict[str, str],
    body: str,
    *,
    resolver: Resolver = lookup,
    timeout: float = _TIMEOUT_SECONDS,
) -> int:
    """Send one message and give back the status of the answer (2xx), or raise DeliveryFailed
    saying what went wrong. The address is checked again here, and the call goes to the address
    that was checked."""
    try:
        addresses = public_addresses(target, settings, resolver)
    except AddressRefused as refusal:
        raise DeliveryFailed(f"Not sent: {refusal}") from None
    connection = _Pinned(target, addresses[0], timeout)
    try:
        connection.request(
            "POST",
            target.path,
            body=body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Vrum-Webhooks/1",
                **headers,
            },
        )
        answer = connection.getresponse()
        answer.read(_MOST_ANSWER_BYTES)
        status = answer.status
    except ssl.SSLCertVerificationError:
        raise DeliveryFailed("The receiver's certificate was not accepted.") from None
    except ssl.SSLError:
        raise DeliveryFailed("The secure connection to the receiver failed.") from None
    except TimeoutError:
        raise DeliveryFailed("The receiver did not answer in time.") from None
    except (OSError, http.client.HTTPException):
        raise DeliveryFailed("The receiver could not be reached.") from None
    finally:
        connection.close()
    if 300 <= status < 400:
        raise DeliveryFailed(
            f"The receiver answered {status}, a redirect, which is not followed.", status
        )
    if not 200 <= status < 300:
        raise DeliveryFailed(f"The receiver answered {status}.", status)
    return status
