"""
One-off helper: generates a VAPID keypair for the Customer Portal's optional
push notifications (new order confirmations, new-device login alerts, etc.).

This is entirely optional - the whole app, including the Customer Portal,
works fine without it. Push notifications just stay silently disabled until
these two values are set as environment variables.

Uses only the `cryptography` package (already installed as a dependency of
firebase-admin - no extra install needed) to generate a standard P-256 EC
keypair in the raw/PEM formats the Web Push protocol expects.

Run:
    python generate_vapid_keys.py

Then copy the two printed values into Render > Environment as TWO separate
variables: VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY. Optionally also set
VAPID_CLAIMS_SUB=mailto:your-real-email@example.com (shown to push
services only if your app is ever misbehaving - never shown to customers).
"""

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization


def main():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    raw_public = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode()

    print("== VAPID keys generated ==\n")
    print(f"VAPID_PUBLIC_KEY={public_b64}\n")
    print("VAPID_PRIVATE_KEY=" + private_pem.replace("\n", "\\n"))
    print(
        "\nCopy both lines into Render > Environment as TWO separate variables.\n"
        "For VAPID_PRIVATE_KEY, paste the value exactly as printed above "
        "(including every \\n) - pywebpush converts it back to real newlines "
        "when the app starts."
    )


if __name__ == "__main__":
    main()
