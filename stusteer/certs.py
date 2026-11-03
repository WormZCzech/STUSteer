"""HTTPS certificates for the phone connection.

Why HTTPS at all: Chrome only gives a web page the phone's motion sensors (the tilt steering) in a
"secure context", which means HTTPS (or localhost). On a home network there is no public
certificate, so we make our own:

1. A tiny local certificate authority (CA), created once: ca.crt + ca.key in the STUSteer folder.
2. A server certificate for this PC's LAN addresses, signed by that CA. It is re-made when the PC's
   IP addresses change (DHCP), and the CA stays the same.

Two ways to use it on the phone:
- Quick: open the page, Chrome warns ("Your connection is not private"), tap Advanced -> Proceed.
- Permanent: download https://<pc>:8443/ca.crt on the phone and install it as a CA certificate
  (Settings -> Security -> Encryption & credentials -> Install a certificate -> CA certificate).
  Chrome then trusts the receiver with no warning. The CA key never leaves the PC; anyone who has
  ca.key could impersonate sites to that phone, which is why it stays in your user folder and the
  CA is limited (name constraints) to your private IP ranges.

Needs the 'cryptography' package (pip). Without it, run the receiver with --http (tilt then needs a
Chrome flag, see README).
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
from pathlib import Path


def ensure_certificates(folder: Path, addresses: list[str]) -> tuple[Path, Path]:
    """Make sure the CA and a server certificate covering `addresses` exist; return (cert, key) paths."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ca_crt, ca_key = folder / "ca.crt", folder / "ca.key"
    srv_crt, srv_key, srv_meta = folder / "server.crt", folder / "server.key", folder / "server.json"
    now = dt.datetime.now(dt.timezone.utc)

    def pem_key(key) -> bytes:
        return key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )

    if ca_crt.exists() and ca_key.exists():
        ca_cert = x509.load_pem_x509_certificate(ca_crt.read_bytes())
        ca_private = serialization.load_pem_private_key(ca_key.read_bytes(), password=None)
    else:
        ca_private = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "STUSteer local CA")])
        private_nets = [
            x509.IPAddress(ipaddress.ip_network(n))
            for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
        ]
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(ca_private.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_private.public_key()), critical=False)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.NameConstraints(permitted_subtrees=[x509.DNSName("localhost"), *private_nets],
                                     excluded_subtrees=None),
                critical=True,
            )
            .sign(ca_private, hashes.SHA256())
        )
        ca_key.write_bytes(pem_key(ca_private))
        ca_crt.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

    wanted = sorted(set(addresses) | {"127.0.0.1"})
    if srv_crt.exists() and srv_key.exists() and srv_meta.exists():
        try:
            meta = json.loads(srv_meta.read_text(encoding="utf-8"))
            fresh = meta.get("expires", 0) - now.timestamp() > 30 * 86400
            if fresh and meta.get("addresses") == wanted and meta.get("ca_serial") == ca_cert.serial_number:
                return srv_crt, srv_key
        except (OSError, json.JSONDecodeError):
            pass

    server_private = ec.generate_private_key(ec.SECP256R1())
    san = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(a)) for a in wanted]
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "STUSteer receiver")]))
        .issuer_name(ca_cert.subject)
        .public_key(server_private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=397))  # browsers reject longer server certificates
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_private.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_private.public_key()), critical=False
        )
        .sign(ca_private, hashes.SHA256())
    )
    srv_key.write_bytes(pem_key(server_private))
    srv_crt.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    meta = {
        "addresses": wanted,
        "ca_serial": ca_cert.serial_number,
        "expires": server_cert.not_valid_after_utc.timestamp(),
    }
    srv_meta.write_text(json.dumps(meta), encoding="utf-8")
    return srv_crt, srv_key
