"""真实本地 TLS SMTP 接收与调度数据库测试；不向外部邮箱投递。"""
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import ipaddress
import socketserver
import ssl
import threading

import pytest

from scripts.workspace_example_cases import generate_cases
from tests.database_migration_helpers import migrated_profile_database, migrated_webui_database


@pytest.fixture(scope="module")
def smtp_receiver(tmp_path_factory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    root = tmp_path_factory.mktemp("local-smtp")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                   .not_valid_after(now + timedelta(days=1))
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                   .sign(key, hashes.SHA256()))
    cert_path, key_path = root / "certificate.pem", root / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    client_context = ssl.create_default_context(cafile=str(cert_path))
    messages = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b"220 localhost test receiver\r\n")
            while line := self.rfile.readline():
                command = line.split(b" ", 1)[0].strip().upper()
                if command in (b"EHLO", b"HELO"):
                    self.wfile.write(b"250-localhost\r\n250 AUTH PLAIN\r\n")
                elif command == b"AUTH":
                    self.wfile.write(b"235 authenticated for local test\r\n")
                elif command in (b"MAIL", b"RCPT", b"RSET"):
                    self.wfile.write(b"250 OK\r\n")
                elif command == b"DATA":
                    self.wfile.write(b"354 end with dot\r\n")
                    body = bytearray()
                    while (line := self.rfile.readline()) not in (b".\r\n", b""):
                        body.extend(line[1:] if line.startswith(b"..") else line)
                    messages.append(BytesParser(policy=policy.default).parsebytes(bytes(body)))
                    self.wfile.write(b"250 received\r\n")
                elif command == b"QUIT":
                    self.wfile.write(b"221 bye\r\n")
                    return
                else:
                    self.wfile.write(b"500 unsupported\r\n")

    class Receiver(socketserver.ThreadingTCPServer):
        daemon_threads = True

        def get_request(self):
            connection, address = super().get_request()
            return server_context.wrap_socket(connection, server_side=True), address

    with Receiver(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server.server_address[1], messages, client_context
        finally:
            server.shutdown()
            worker.join(timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [case for case in generate_cases() if case["kind"] == "email"], ids=lambda case: case["id"])
async def test_email_dispatch_matches_authorized_result(case, smtp_receiver, monkeypatch, tmp_path):
    from src.account_execution import execution_context
    from src.api.store import WebUIStore
    from src.conductor import email_sender
    from src.notifications import Intent, dispatch
    port, messages, tls = smtp_receiver
    before = len(messages)
    for key, value in dict(smtp_enabled=True, smtp_host="127.0.0.1", smtp_port=port, smtp_use_ssl=True,
                           smtp_user="sender@example.invalid", smtp_from="", smtp_password="synthetic").items():
        monkeypatch.setattr(email_sender.settings, key, value)
    # 信任仅供测试的本地证书；仍校验 TLS 证书与主机，未关闭安全校验。
    monkeypatch.setattr(email_sender.ssl, "create_default_context", lambda: tls)
    store = WebUIStore(str(migrated_webui_database(tmp_path / "users.db")))
    owner = store.create_user("mail-fixture", "synthetic-unused-hash")["user_id"]
    expected = case["expected"]
    intent = Intent(channel="none" if expected["action"] == "none" else "email", recipients=expected["recipients"],
                    body=expected["body"], attachments=expected["attachments"],
                    question="请提供有效邮箱" if expected["action"] == "ask_recipient" else "")
    attachment = tmp_path / "report.csv"
    attachment.write_text("项目,金额\n交通,20\n", encoding="utf-8")
    with execution_context(store.capture_account_execution(owner)):
        result = await dispatch(store, owner, case["id"], intent, title="合成报销报告", body="测试正文", attachments=[str(attachment)])
        replay = await dispatch(store, owner, case["id"], intent, title="合成报销报告", body="测试正文", attachments=[str(attachment)])
    if expected["action"] == "confirm":
        assert result["status"] == replay["status"] == "sent"
        assert len(messages) == before + 1
        message = messages[-1]
        assert str(message["To"]) == ", ".join(expected["recipients"])
        assert message.get_body().get_content().strip() == ("测试正文" if expected["body"] else "请查收附件。")
        attachments = list(message.iter_attachments())
        assert len(attachments) == int(expected["attachments"])
        if attachments:
            assert attachments[0].get_payload(decode=True) == attachment.read_bytes()
    else:
        assert result["status"] == replay["status"] == "needs_input"
        assert len(messages) == before


@pytest.mark.parametrize("case", [case for case in generate_cases() if case["kind"] == "schedule"], ids=lambda case: case["id"])
def test_schedule_is_persisted_due_once_and_keeps_owner(case, tmp_path):
    from src.scheduler.cron import compute_next_run, parse_schedule
    from src.scheduler.store import ScheduleStore
    from tests.scheduler_helpers import scheduler_owner
    value = case["expected"]["schedule"]
    if value is None:
        with pytest.raises(ValueError):
            parse_schedule("cron@70 25 * * 1" if case["variant"] == "type_error" else "")
        return
    seed = int(case["id"].rsplit("-", 1)[1]) - 1
    if case["variant"] == "boundary":
        expected = (datetime(2026, 9, 27), datetime(2026, 9, 27, 23, 59), datetime(2026, 9, 28),
                    datetime(2026, 9, 25, 23, 59), datetime(2026, 9, 26, 0, 1))[seed]
    elif case["variant"] == "constraint":
        expected = datetime(2026, 9, 21, 9, 30 + seed)
    elif case["variant"] == "combination":
        expected = datetime(2026, 9, 22, 10, 25 + seed)
    else:
        expected = datetime(2026, 9, 21, 9, 25 + seed)
    specification = parse_schedule(value)
    assert compute_next_run(specification, datetime(2026, 9, 21)) == expected
    with scheduler_owner(tmp_path / "users.db") as owner:
        database = migrated_profile_database(tmp_path / "scheduler.db", profile="scheduler")
        store = ScheduleStore(str(database))
        identity = store.add(owner_user_id=owner["user_id"], user_input=case["sources"][0]["content"], provider=None, model=None,
                             trigger_type="cron", cron_expr=specification.cron_expr, run_at=None, next_run_at=expected)
        reopened = ScheduleStore(str(database))
        assert reopened.get(identity)["owner_user_id"] == owner["user_id"]
        assert not reopened.due_tasks(now=expected - timedelta(seconds=1))
        assert [task["task_id"] for task in reopened.due_tasks(now=expected)] == [identity]
        reopened.set_status(identity, "paused")
        assert not reopened.due_tasks(now=expected)
