"""Continuous synthetic traffic against the payment chain.

Two of the four controlled faults are only observable on a real request path:

  * `auth-key-error` corrupts the JWT signing key, which surfaces only when a token is validated.
  * `notification-latency` delays a downstream call, which surfaces only when payment-service
    makes one.

On an idle estate neither fault produces any metric at all - Micrometer does not create a timer
series until the code path executes - so the Grafana panels have nothing to draw and injecting
those faults appears to do nothing. This generator keeps a steady, modest request rate flowing so
the signals exist.

It mirrors `telemetry/payment_probe.py` in the one behaviour that matters for fault realism: the
JWT is **cached across iterations** rather than re-fetched each time, so a token obtained before
an auth-key-error injection continues to be presented and begins to fail - exactly what an
already-authenticated client experiences.

Standard library only, so the container needs no package installation.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

AUTH = os.environ.get("AUTH_URL", "http://auth-service:8081")
PAYMENT = os.environ.get("PAYMENT_URL", "http://payment-service:8082")
LEDGER = os.environ.get("LEDGER_URL", "http://ledger-service:8083")
USERNAME = os.environ.get("LOAD_USERNAME", "alice")
PASSWORD = os.environ.get("LOAD_PASSWORD", "alice-pass")
ACCOUNT = os.environ.get("LOAD_ACCOUNT", "acct-42")
INTERVAL = float(os.environ.get("LOAD_INTERVAL_SECONDS", "2"))
AMOUNT = float(os.environ.get("LOAD_AMOUNT", "1.00"))
TOPUP_EVERY = int(os.environ.get("LOAD_TOPUP_EVERY", "40"))
TOPUP_AMOUNT = float(os.environ.get("LOAD_TOPUP_AMOUNT", "200.00"))
# Off by default - see the 401 branch in main() for why re-authenticating hides auth-key-error.
REFRESH_ON_401 = os.environ.get("LOAD_REFRESH_ON_401", "false").lower() == "true"


def _post(url: str, payload: dict, token: str | None = None, timeout: float = 10.0):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:  # noqa: BLE001 - a service being down is an expected state here
        return None, None


def login() -> str | None:
    status, body = _post(f"{AUTH}/api/auth/login",
                         {"username": USERNAME, "password": PASSWORD})
    if status == 200 and body:
        return body.get("token")
    return None


def main() -> None:
    print(f"[load] payment chain, every {INTERVAL}s, {AMOUNT} per payment", flush=True)
    token: str | None = None
    ok = failed = 0
    for i in range(1, 10**9):
        if token is None:
            token = login()
            if token is None:
                print("[load] auth unavailable; retrying", flush=True)
                time.sleep(INTERVAL)
                continue
            print("[load] acquired a token", flush=True)

        status, _ = _post(f"{PAYMENT}/api/payments", token=token, payload={
            "accountId": ACCOUNT, "currency": "USD", "amount": AMOUNT})

        if status == 200:
            ok += 1
        else:
            failed += 1
            # Deliberately do NOT re-authenticate on 401.
            #
            # `auth-key-error` corrupts the JWT *signing* key, so a token obtained *after* the
            # injection is signed and validated with the same corrupted key and succeeds. Only
            # tokens issued beforehand fail. A client that re-logs-in on 401 therefore heals
            # itself within one request and the fault becomes invisible - which is exactly what
            # happened the first time this generator ran.
            #
            # `telemetry/payment_probe.py` caches its token across cycles for this reason: a token
            # held by an already-authenticated user keeps being presented and keeps failing, which
            # is both the realistic client behaviour and the only way the fault produces a
            # sustained error rate. The token is refreshed only when the fault is cleared.
            if status == 401 and REFRESH_ON_401:
                token = None

        # Keep the demo account solvent so the error rate reflects injected faults rather than a
        # drained balance.
        # If every request has been failing for a long stretch the token may genuinely be
        # expired rather than fault-affected, so renew it occasionally - slowly enough that an
        # injected fault still shows a clear, sustained error rate.
        if failed and failed % 150 == 0:
            print("[load] sustained failures; renewing token", flush=True)
            token = None

        if i % TOPUP_EVERY == 0:
            _post(f"{LEDGER}/api/ledger/credit",
                  {"accountId": ACCOUNT, "currency": "USD", "amount": TOPUP_AMOUNT})
            print(f"[load] {ok} ok / {failed} failed; topped up {TOPUP_AMOUNT}", flush=True)

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
