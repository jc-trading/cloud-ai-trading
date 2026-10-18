"""SMTP channel against an in-process fake SMTP server (P4, M5).

The fake speaks just enough ESMTP for smtplib: EHLO/HELO, STARTTLS (advertised
only in plaintext), AUTH PLAIN/LOGIN (advertised and accepted only after TLS),
MAIL/RCPT/DATA/RSET/NOOP/QUIT. TLS uses a throwaway self-signed certificate for
127.0.0.1 that the client trusts via a patched ``_ssl_context``. Never touches a
real mail server.
"""

import asyncio
import base64
import datetime as dt
from email import message_from_bytes, policy as email_policy
import ipaddress
from pathlib import Path
import socket
import socketserver
import ssl
import sys
import threading
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.modules.notifications import channels as ch


@pytest.fixture(autouse=True)
def _no_real_secrets(monkeypatch):
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY",
                "SMTP_USER", "SMTP_APP_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(settings, key, "")


@pytest.fixture(scope="module")
def cert(tmp_path_factory):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = dt.datetime.now(dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName(
            [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                       critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                       critical=False)
        .sign(key, hashes.SHA256())
    )
    d = tmp_path_factory.mktemp("smtp_cert")
    cert_path, key_path = d / "cert.pem", d / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return SimpleNamespace(cert=str(cert_path), key=str(key_path))


class _Handler(socketserver.StreamRequestHandler):
    def _send(self, line):
        self.sock.sendall((line + "\r\n").encode())

    def _readline(self):
        return self.reader.readline().decode("utf-8", "replace").rstrip("\r\n")

    def handle(self):
        srv = self.server
        self.sock = self.request
        self.reader = self.sock.makefile("rb")
        tls = False
        self._send("220 fake ESMTP")
        while True:
            line = self._readline()
            if not line:
                return
            verb = line.split(" ", 1)[0].upper()
            srv.log.append(verb)
            if verb == "EHLO":
                caps = ["fake"]
                if not tls and srv.offer_starttls:
                    caps.append("STARTTLS")
                if tls:
                    caps.append("AUTH PLAIN LOGIN")
                for c in caps:
                    self._send(f"250-{c}")
                self._send("250 OK")
            elif verb == "HELO":
                self._send("250 fake")
            elif verb == "STARTTLS":
                if tls or not srv.offer_starttls:
                    self._send("502 not supported")
                    continue
                self._send("220 go ahead")
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(srv.cert.cert, srv.cert.key)
                self.sock = ctx.wrap_socket(self.sock, server_side=True)
                self.reader = self.sock.makefile("rb")
                tls = True
            elif verb == "AUTH":
                if not tls:
                    self._send("530 must issue STARTTLS first")
                    continue
                srv.tls_active_at_auth = tls
                parts = line.split(" ")
                if parts[1].upper() == "PLAIN":
                    blob = parts[2] if len(parts) > 2 else self._ask("")
                    _, user, pw = base64.b64decode(blob).decode().split("\0")
                else:
                    user = base64.b64decode(self._ask("VXNlcm5hbWU6")).decode()
                    pw = base64.b64decode(self._ask("UGFzc3dvcmQ6")).decode()
                srv.creds = (user, pw)
                self._send("535 5.7.8 bad credentials" if srv.reject_auth else "235 ok")
            elif verb == "MAIL":
                srv.mail_from = line
                self._send("250 ok")
            elif verb == "RCPT":
                srv.rcpts.append(line)
                self._send("250 ok")
            elif verb == "DATA":
                self._send("354 end with .")
                body = []
                while (chunk := self._readline()) != ".":
                    body.append(chunk)
                srv.data = "\n".join(body)
                self._send("250 queued")
            elif verb in ("RSET", "NOOP"):
                self._send("250 ok")
            elif verb == "QUIT":
                self._send("221 bye")
                return
            else:
                self._send("500 unknown")

    def _ask(self, prompt):
        self._send(f"334 {prompt}")
        return self._readline()


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


@pytest.fixture
def smtp_server(cert, monkeypatch):
    def start(offer_starttls=True, reject_auth=False):
        srv = _Server(("127.0.0.1", 0), _Handler)
        srv.cert = cert
        srv.offer_starttls = offer_starttls
        srv.reject_auth = reject_auth
        srv.log, srv.rcpts = [], []
        srv.creds = srv.mail_from = srv.data = srv.tls_active_at_auth = None
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv

    servers = []
    monkeypatch.setattr(ch, "_ssl_context",
                        lambda: ssl.create_default_context(cafile=cert.cert))
    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def sleeps(monkeypatch):
    calls = []

    async def fake_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr(ch, "_sleep", fake_sleep)
    return calls


def _channel(port, to=()):
    return SimpleNamespace(type="smtp", name="gmail", secret_env_key="SMTP_APP_PASSWORD",
                           config_json={"host": "127.0.0.1", "port": port,
                                        "username_env": "SMTP_USER", "to": list(to)})


def _creds(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_APP_PASSWORD", "app-pass-123")


def test_delivers_over_starttls_with_subject_body_and_recipients(smtp_server, sleeps,
                                                                 monkeypatch):
    _creds(monkeypatch)
    srv = smtp_server()
    out = asyncio.run(ch.send_via(_channel(srv.server_address[1], to=["ops@example.com"]),
                                  "data_fail_closed", "critical", "⛔ cycle refused"))
    assert (out.ok, out.attempts, out.error) == (True, 1, None)
    assert srv.tls_active_at_auth is True
    assert srv.log.index("STARTTLS") < srv.log.index("AUTH")
    assert srv.creds == ("bot@example.com", "app-pass-123")
    assert "bot@example.com" in srv.mail_from
    assert [r.upper() for r in srv.rcpts] == ["RCPT TO:<OPS@EXAMPLE.COM>"]
    assert sleeps == []


def test_subject_and_default_recipient(smtp_server, sleeps, monkeypatch):
    _creds(monkeypatch)
    srv = smtp_server()
    out = asyncio.run(ch.send_via(_channel(srv.server_address[1]), "fill_entry", "info",
                                  "📈 entries booked: AAA"))
    assert out.ok
    msg = message_from_bytes(srv.data.encode(), policy=email_policy.default)
    assert msg["Subject"] == "[CAT] INFO · fill_entry"
    assert msg["To"] == "bot@example.com"
    assert msg.get_content().strip() == "📈 entries booked: AAA"
    assert [r.upper() for r in srv.rcpts] == ["RCPT TO:<BOT@EXAMPLE.COM>"]


def test_no_starttls_offered_fails_and_never_authenticates(smtp_server, sleeps,
                                                           monkeypatch):
    _creds(monkeypatch)
    srv = smtp_server(offer_starttls=False)
    out = asyncio.run(ch.send_via(_channel(srv.server_address[1]), "stop_hit", "warning",
                                  "x"))
    assert out.ok is False
    assert out.attempts == 4
    assert "STARTTLS" in out.error
    assert "AUTH" not in srv.log
    assert srv.creds is None
    assert sleeps == [1, 5, 30]


def test_auth_rejected_stops_after_one_attempt(smtp_server, sleeps, monkeypatch):
    _creds(monkeypatch)
    srv = smtp_server(reject_auth=True)
    out = asyncio.run(ch.send_via(_channel(srv.server_address[1]), "stop_hit", "warning",
                                  "x"))
    assert (out.ok, out.attempts) == (False, 1)
    assert "SMTPAuthenticationError" in out.error
    assert srv.log.count("STARTTLS") == 1
    assert sleeps == []


def test_closed_port_retries_with_backoff(sleeps, monkeypatch):
    _creds(monkeypatch)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    out = asyncio.run(ch.send_via(_channel(port), "stop_hit", "warning", "x"))
    assert (out.ok, out.attempts) == (False, 4)
    assert "ConnectionRefusedError" in out.error
    assert sleeps == [1, 5, 30]


@pytest.mark.parametrize("unset", ["SMTP_USER", "SMTP_APP_PASSWORD"])
def test_missing_credentials_fail_before_connecting(unset, sleeps, monkeypatch):
    _creds(monkeypatch)
    monkeypatch.delenv(unset)
    out = asyncio.run(ch.send_via(_channel(1), "stop_hit", "warning", "x"))
    assert (out.ok, out.attempts, out.error) == (False, 0, f"{unset} not set")
