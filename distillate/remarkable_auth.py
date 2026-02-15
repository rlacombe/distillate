"""One-time reMarkable device registration.

Flow:
  1. User visits https://my.remarkable.com/device/browser/connect
  2. User pastes the one-time code here
  3. We exchange it for a long-lived device token
  4. We save the token to .env
"""

import uuid

import requests

from distillate.config import save_to_env

_DEVICE_TOKEN_URL = "https://webapp-prod.cloud.remarkable.engineering/token/json/2/device/new"
_USER_TOKEN_URL = "https://webapp-prod.cloud.remarkable.engineering/token/json/2/user/new"


def register_device(code: str) -> str:
    """Exchange a one-time code for a device token."""
    resp = requests.post(
        _DEVICE_TOKEN_URL,
        json={
            "code": code,
            "deviceDesc": "desktop-macos",
            "deviceID": str(uuid.uuid4()),
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Registration failed ({resp.status_code}): {resp.text}")
    return resp.text.strip()


def get_user_token(device_token: str) -> str:
    """Exchange a device token for a short-lived session token."""
    resp = requests.post(
        _USER_TOKEN_URL,
        headers={"Authorization": f"Bearer {device_token}"},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh failed ({resp.status_code}): {resp.text}")
    return resp.text.strip()


def register_interactive() -> None:
    """Interactive registration flow. Prompts for code, saves token to .env."""
    print("reMarkable Device Registration")
    print("=" * 40)
    print()
    print("1. Go to: https://my.remarkable.com/device/browser/connect")
    print("2. Copy the one-time code shown on the page")
    print()

    code = input("Paste your one-time code: ").strip()
    if not code:
        print("Error: No code provided.")
        return

    print()
    print("Exchanging code for device token...")
    device_token = register_device(code)

    print("Verifying token...")
    get_user_token(device_token)

    save_to_env("REMARKABLE_DEVICE_TOKEN", device_token)
    print()
    print("Success! Device token saved to .env")
