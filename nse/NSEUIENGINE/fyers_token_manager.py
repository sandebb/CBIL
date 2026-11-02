"""
NSEUIENGINE - Fyers API v3 access-token manager.

Run by Task Scheduler every morning at 08:00 (and at logon). It makes sure a valid Fyers access token for today is
in the Windows environment, so any script on this PC can read it:

    FYERS_ACCESS_TOKEN      the access token. REST header:  Authorization: <FYERS_CLIENT_ID>:<FYERS_ACCESS_TOKEN>
    FYERS_WS_TOKEN          "<FYERS_CLIENT_ID>:<token>"  for FyersDataSocket / the TBT socket / the REST header
    FYERS_TOKEN_DATE        IST date the token was issued (yyyy-mm-dd)
    FYERS_TOKEN_ISSUED_AT   IST timestamp the token was issued

Each run first checks whether these variables exist (missing ones are created), then refreshes the token.
Order of attempts, first that works wins:
  0. the token already in the environment, if issued today and accepted by /profile   (skipped with --force)
  1. refresh token     - official API v3 'validate-refresh-token' (refresh token valid 15 days, needs the PIN)
  2. headless login    - Fyers ID + TOTP + PIN -> auth code -> 'validate-authcode' (also renews the refresh token)
  3. browser login     - only with --interactive: opens the Fyers login page, captures auth_code on redirect_uri

Configuration
  Non-secret, user environment variables (written by --setup):
      FYERS_CLIENT_ID      app id from myapi.fyers.in, e.g. ABCD1234-100   (same name NSEALGOENGINE uses)
      FYERS_ID             your Fyers login/client id, e.g. XY12345
      FYERS_REDIRECT_URI   must match the app's redirect URL (default http://127.0.0.1:5000/)
  Secrets: state\\fyers_secrets.bin, encrypted with Windows DPAPI (readable only by your Windows account on this PC):
      secret_key, pin, totp_secret, refresh_token
      Fallback if not in the file: environment variables FYERS_SECRET_KEY, FYERS_PIN, FYERS_TOTP_SECRET.

Usage
  python fyers_token_manager.py                 scheduled run (no prompts)
  python fyers_token_manager.py --force         get a new token even if today's is valid
  python fyers_token_manager.py --interactive   allow the browser login as last resort (first-time app approval)
  python fyers_token_manager.py --setup         enter / change credentials
  python fyers_token_manager.py --status        show what is set (tokens masked)
  --scope machine                               write HKLM variables (all users; needs an elevated run)
  --proxy                                       send login calls through %STATICIP_PROXY%

Tokens and secrets are never written to the log.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import base64
import ctypes
import getpass
import hashlib
import json
import logging
import logging.handlers
import os
import sys
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

__version__ = "1.0.0"

IST = timezone(timedelta(hours=5, minutes=30))
API = "https://api-t1.fyers.in/api/v3"
VAGATOR = "https://api-t2.fyers.in/vagator/v2"
DEFAULT_REDIRECT = "http://127.0.0.1:5000/"
REFRESH_TOKEN_DAYS = 15          # Fyers: refresh token valid for 15 days
REFRESH_TOKEN_USE_DAYS = 14      # stop using it a day early

TOKEN_VARS = ("FYERS_ACCESS_TOKEN", "FYERS_WS_TOKEN", "FYERS_TOKEN_DATE", "FYERS_TOKEN_ISSUED_AT")
SENSITIVE_VARS = ("FYERS_ACCESS_TOKEN", "FYERS_WS_TOKEN")
SECRET_ENV_FALLBACK = {"secret_key": "FYERS_SECRET_KEY", "pin": "FYERS_PIN", "totp_secret": "FYERS_TOTP_SECRET"}


def _home() -> Path:
    h = os.environ.get("NSEUIENGINE_HOME")
    if h and Path(h).is_dir():
        return Path(h)
    return Path(__file__).resolve().parent


HOME = _home()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fyers_token import _read as _envv, data_dir as _data_dir  # noqa: E402
DATA = _data_dir()   # shared data area, else HOME
STATE_DIR = DATA / "state"
LOG_DIR = DATA / "logs"
SECRETS_FILE = STATE_DIR / "fyers_secrets.bin"
STATUS_FILE = STATE_DIR / "fyers_token_status.json"

log = logging.getLogger("nseui.fyers_token")


class FyersAuthError(Exception):
    """Fyers refused something (bad PIN/TOTP/secret, unapproved app). Never retried automatically: repeated wrong
    PIN/TOTP attempts can lock the Fyers account."""


class ConfigError(Exception):
    pass


def now_ist() -> datetime:
    return datetime.now(IST)


def mask(v: str | None) -> str:
    if not v:
        return "<not set>"
    return f"{v[:6]}...{v[-4:]} ({len(v)} chars)" if len(v) > 14 else "****"


# ------------------------------------------------------------------------------------------------------------------
# Windows environment variables (registry) - persistent, visible to every new process
# ------------------------------------------------------------------------------------------------------------------
class WinEnv:
    USER = r"Environment"
    MACHINE = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

    def __init__(self, scope: str = "user"):
        import winreg
        self.w = winreg
        self.scope = scope
        if scope == "machine":
            self.root, self.path = winreg.HKEY_LOCAL_MACHINE, self.MACHINE
        else:
            self.root, self.path = winreg.HKEY_CURRENT_USER, self.USER

    def get(self, name: str) -> str | None:
        try:
            with self.w.OpenKey(self.root, self.path) as k:
                return str(self.w.QueryValueEx(k, name)[0])
        except FileNotFoundError:
            return None

    def set(self, name: str, value: str) -> None:
        try:
            with self.w.OpenKey(self.root, self.path, 0, self.w.KEY_SET_VALUE) as k:
                self.w.SetValueEx(k, name, 0, self.w.REG_SZ, value)
        except PermissionError as ex:
            raise ConfigError(f"no permission to write {self.scope} environment variable {name}"
                              + (" (run elevated for --scope machine)" if self.scope == "machine" else "")) from ex
        os.environ[name] = value

    def broadcast(self) -> None:
        """Tell Explorer and other top-level windows the environment changed, so programs started from now on
        (new terminals, IDEs, scheduled tasks) get the new values. Already-running processes keep their old copy."""
        try:
            res = ctypes.c_ulong()
            ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000,
                                                     ctypes.byref(res))
        except Exception as ex:  # no desktop (e.g. run while logged off) - harmless
            log.debug("WM_SETTINGCHANGE broadcast failed: %s", ex)


def read_any_env(name: str) -> str | None:
    """Process env first, then the user and machine registry (catches values set after this process started)."""
    v = os.environ.get(name)
    if v:
        return v
    if os.name == "nt":
        for scope in ("user", "machine"):
            try:
                v = WinEnv(scope).get(name)
            except Exception:
                v = None
            if v:
                return v
    return None


# ------------------------------------------------------------------------------------------------------------------
# Secrets file protected with Windows DPAPI (CryptProtectData, current-user scope)
# ------------------------------------------------------------------------------------------------------------------
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


_ENTROPY = b"NSEUIENGINE/fyers/v1"


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_wchar_p, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_uint32, ctypes.POINTER(_Blob)]
    fn.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf_in, buf_ent = ctypes.create_string_buffer(data, len(data)), ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    blob_in = _Blob(len(data), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
    blob_ent = _Blob(len(_ENTROPY), ctypes.cast(buf_ent, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    desc = "NSEUIENGINE fyers" if protect else None
    if not fn(ctypes.byref(blob_in), desc, ctypes.byref(blob_ent), None, None, 0x1, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


class SecretStore:
    def __init__(self, path: Path = SECRETS_FILE):
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(_dpapi(self.path.read_bytes(), protect=False).decode("utf-8"))
        except Exception as ex:
            raise ConfigError(f"cannot decrypt {self.path.name} ({ex}). It only opens under the Windows account "
                              f"that created it; re-run --setup if the account or PC changed.") from ex

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(_dpapi(json.dumps(data).encode("utf-8"), protect=True))
        os.replace(tmp, self.path)

    def update(self, **kv) -> None:
        d = self.load()
        d.update({k: v for k, v in kv.items() if v is not None})
        self.save(d)


# ------------------------------------------------------------------------------------------------------------------
# Fyers API v3 authentication
# ------------------------------------------------------------------------------------------------------------------
def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _msg(d) -> str:
    if isinstance(d, dict):
        return str(d.get("message") or d.get("msg") or d.get("code") or d)[:300]
    return str(d)[:300]


class FyersAuth:
    def __init__(self, app_id: str, secret_key: str, redirect_uri: str = DEFAULT_REDIRECT, fy_id: str | None = None,
                 pin: str | None = None, totp_secret: str | None = None, proxy: str | None = None,
                 timeout: float = 20.0):
        self.app_id, self.secret_key, self.redirect_uri = app_id.strip(), secret_key.strip(), redirect_uri.strip()
        self.fy_id, self.pin, self.totp_secret = fy_id, pin, totp_secret
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": f"NSEUIENGINE/{__version__}", "Accept": "application/json"})
        if proxy:
            self.s.proxies.update({"http": proxy, "https": proxy})

    @property
    def app_id_hash(self) -> str:
        """Fyers v3 appIdHash = SHA-256 of "<app_id>:<secret_key>"."""
        return hashlib.sha256(f"{self.app_id}:{self.secret_key}".encode()).hexdigest()

    @property
    def can_headless(self) -> bool:
        return bool(self.fy_id and self.pin and self.totp_secret)

    def _post(self, url: str, payload: dict, **kw):
        r = self.s.post(url, json=payload, timeout=self.timeout, **kw)
        try:
            d = r.json()
        except ValueError:
            if r.status_code >= 500:
                raise requests.HTTPError(f"{url}: HTTP {r.status_code}")      # transient: retried
            raise FyersAuthError(f"{url.rsplit('/', 1)[-1]}: HTTP {r.status_code}, non-JSON reply")
        return r, d

    # -- token checks -------------------------------------------------------------------------------------------
    def validate(self, token: str) -> dict | None:
        """Profile call with the token; returns the profile dict if Fyers accepts it, else None."""
        r = self.s.get(f"{API}/profile", headers={"Authorization": f"{self.app_id}:{token}"}, timeout=self.timeout)
        try:
            d = r.json()
        except ValueError:
            return None
        return (d.get("data") or {"ok": True}) if d.get("s") == "ok" else None

    # -- 1. refresh token -----------------------------------------------------------------------------------------
    def from_refresh_token(self, refresh_token: str) -> str:
        if not self.pin:
            raise FyersAuthError("refresh token needs the PIN (run --setup)")
        _, d = self._post(f"{API}/validate-refresh-token", {"grant_type": "refresh_token",
                                                             "appIdHash": self.app_id_hash,
                                                             "refresh_token": refresh_token, "pin": self.pin})
        if d.get("s") != "ok" or not d.get("access_token"):
            raise FyersAuthError(f"refresh token rejected: {_msg(d)}")
        return d["access_token"]

    # -- auth code -> tokens ----------------------------------------------------------------------------------------
    def exchange_auth_code(self, code: str) -> dict:
        _, d = self._post(f"{API}/validate-authcode", {"grant_type": "authorization_code",
                                                        "appIdHash": self.app_id_hash, "code": code})
        if d.get("s") != "ok" or not d.get("access_token"):
            raise FyersAuthError(f"validate-authcode failed: {_msg(d)} (check FYERS_CLIENT_ID and the secret key)")
        return {"access_token": d["access_token"], "refresh_token": d.get("refresh_token")}

    # -- 2. headless login ------------------------------------------------------------------------------------------
    def headless_auth_code(self) -> str:
        """Login as the Fyers web/app login does: send_login_otp -> verify TOTP -> verify PIN -> /token (auth code).
        These 'vagator' endpoints are what Fyers' own login page uses; they are not part of the published API, so
        Fyers can change them. The refresh-token and browser paths do not depend on them."""
        if not self.can_headless:
            raise ConfigError("headless login needs FYERS_ID, PIN and TOTP secret (run --setup)")
        _, d = self._post(f"{VAGATOR}/send_login_otp_v2", {"fy_id": _b64(self.fy_id), "app_id": "2"})
        if d.get("s") != "ok" or not d.get("request_key"):
            raise FyersAuthError(f"send_login_otp failed: {_msg(d)} (check FYERS_ID)")
        d = self._verify_totp(d["request_key"])
        _, d = self._post(f"{VAGATOR}/verify_pin_v2", {"request_key": d["request_key"], "identity_type": "pin",
                                                        "identifier": _b64(self.pin)})
        login_token = (d.get("data") or {}).get("access_token") if d.get("s") == "ok" else None
        if not login_token:
            raise FyersAuthError(f"PIN rejected: {_msg(d)}")
        app, _, app_type = self.app_id.rpartition("-")
        if not app:                                    # app id without the "-100" suffix
            app, app_type = self.app_id, "100"
        r, d = self._post(f"{API}/token",
                          {"fyers_id": self.fy_id, "app_id": app, "redirect_uri": self.redirect_uri,
                           "appType": app_type, "code_challenge": "", "state": "nseuiengine", "scope": "",
                           "nonce": "", "response_type": "code", "create_cookie": True},
                          headers={"Authorization": f"Bearer {login_token}"}, allow_redirects=False)
        url = (d.get("Url") or d.get("url") or r.headers.get("Location") or "") if isinstance(d, dict) else ""
        code = parse_qs(urlparse(url).query).get("auth_code", [None])[0]
        if not code:
            raise FyersAuthError(f"no auth_code from /token: {_msg(d)}. If the app was never approved, run once "
                                 f"with --interactive and accept the consent screen; also check the redirect URI.")
        return code

    def _verify_totp(self, request_key: str) -> dict:
        import pyotp
        totp = pyotp.TOTP(self.totp_secret.replace(" ", "").upper())
        last = None
        for attempt in range(2):                     # at most one retry, in the next 30 s window
            left = totp.interval - (time.time() % totp.interval)
            if left < 4 or attempt:
                time.sleep(left + 1)
            _, last = self._post(f"{VAGATOR}/verify_otp", {"request_key": request_key, "otp": totp.now()})
            if last.get("s") == "ok" and last.get("request_key"):
                return last
            log.warning("TOTP not accepted (%s)%s", _msg(last), "; retrying in the next window" if not attempt else "")
        raise FyersAuthError(f"TOTP rejected: {_msg(last)} (check the TOTP secret and that the PC clock is in sync)")

    # -- 3. browser login -------------------------------------------------------------------------------------------
    def browser_auth_code(self, timeout: int = 300) -> str:
        url = f"{API}/generate-authcode?" + urlencode({"client_id": self.app_id, "redirect_uri": self.redirect_uri,
                                                        "response_type": "code", "state": "nseuiengine"})
        print(f"\nLog in to Fyers in the browser (opening now). If it doesn't open, paste this URL:\n  {url}\n")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        u = urlparse(self.redirect_uri)
        if u.hostname in ("127.0.0.1", "localhost") and u.port:
            box: dict = {}

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):  # noqa: N802
                    box.update({k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()})
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"NSEUIENGINE: Fyers login captured. You can close this tab.")

                def log_message(self, *a):
                    pass

            srv = HTTPServer((u.hostname, u.port), Handler)
            srv.timeout = 2
            end = time.time() + timeout
            try:
                while time.time() < end and "auth_code" not in box:
                    srv.handle_request()
            finally:
                srv.server_close()
            if box.get("auth_code"):
                return box["auth_code"]
            raise FyersAuthError(f"no auth_code received on {self.redirect_uri} within {timeout}s ({box or 'nothing'})")
        pasted = input("After logging in, paste the full redirected URL (or just the auth_code): ").strip()
        return parse_qs(urlparse(pasted).query).get("auth_code", [pasted])[0]


# ------------------------------------------------------------------------------------------------------------------
# Orchestration
# ------------------------------------------------------------------------------------------------------------------
def load_settings(store: SecretStore) -> dict:
    sec = store.load()

    def secret(name):
        return _clean(sec.get(name) or read_any_env(SECRET_ENV_FALLBACK[name]) or "") or None

    cfg = {
        "app_id": read_any_env("FYERS_CLIENT_ID"),
        "fy_id": read_any_env("FYERS_ID"),
        "redirect_uri": read_any_env("FYERS_REDIRECT_URI") or DEFAULT_REDIRECT,
        "secret_key": secret("secret_key"),
        "pin": secret("pin"),
        "totp_secret": secret("totp_secret"),
        "refresh_token": sec.get("refresh_token"),
        "refresh_issued": sec.get("refresh_issued"),
    }
    missing = [n for n, k in (("FYERS_CLIENT_ID", "app_id"), ("secret key", "secret_key")) if not cfg[k]]
    if missing:
        raise ConfigError(f"missing {', '.join(missing)} - run: python fyers_token_manager.py --setup")
    return cfg


def refresh_token_usable(cfg: dict, today) -> bool:
    # SEBI retail-algo rules (Fyers, from 1 Apr 2026): 2FA once every trading day; refresh-token sessions are not
    # supported. So the daily TOTP login is used; the refresh token only if FYERS_ALLOW_REFRESH=1 is set.
    if os.environ.get("FYERS_ALLOW_REFRESH", "").strip() != "1":
        return False
    if not (cfg.get("refresh_token") and cfg.get("refresh_issued") and cfg.get("pin")):
        return False
    try:
        age = (today - datetime.fromisoformat(cfg["refresh_issued"]).date()).days
    except ValueError:
        return False
    return 0 <= age <= REFRESH_TOKEN_USE_DAYS


def publish(env, app_id: str, token: str, issued: datetime) -> None:
    env.set("FYERS_ACCESS_TOKEN", token)
    env.set("FYERS_WS_TOKEN", f"{app_id}:{token}")
    env.set("FYERS_TOKEN_DATE", issued.date().isoformat())
    env.set("FYERS_TOKEN_ISSUED_AT", issued.isoformat(timespec="seconds"))
    env.broadcast()


def ensure_token(env, store: SecretStore, auth: FyersAuth | None = None, *, force: bool = False,
                 interactive: bool = False, proxy: str | None = None) -> str:
    """Make sure today's token is in the environment. Returns how it was obtained."""
    cfg = load_settings(store)
    t0 = now_ist()
    today = t0.date()

    # Step 1: check whether the variables exist; create the missing ones (filled in below)
    existing = {n: env.get(n) for n in TOKEN_VARS}
    for n, v in existing.items():
        if v is None:
            log.info("environment variable %s does not exist - creating it", n)
            env.set(n, "")
        else:
            log.info("environment variable %s exists (%s)", n, mask(v) if n in SENSITIVE_VARS else (v or "empty"))

    auth = auth or FyersAuth(cfg["app_id"], cfg["secret_key"], cfg["redirect_uri"], cfg["fy_id"], cfg["pin"],
                             cfg["totp_secret"], proxy=proxy)

    # Step 2: is today's token already there and valid?
    tok = existing.get("FYERS_ACCESS_TOKEN")
    if not force and tok and existing.get("FYERS_TOKEN_DATE") == today.isoformat():
        if auth.validate(tok):
            if existing.get("FYERS_WS_TOKEN") != f"{cfg['app_id']}:{tok}":
                env.set("FYERS_WS_TOKEN", f"{cfg['app_id']}:{tok}")
                env.broadcast()
            log.info("today's token is already valid - nothing to do")
            return "existing"
        log.info("today's token in the environment is no longer accepted - refreshing")

    # Step 3: get a new one
    errors, token, how = [], None, None
    if refresh_token_usable(cfg, today):
        try:
            token, how = auth.from_refresh_token(cfg["refresh_token"]), "refresh token"
        except FyersAuthError as ex:
            errors.append(str(ex))
            log.warning("%s", ex)
    elif cfg.get("refresh_token"):
        log.info("refresh token is older than %d days - doing a full login", REFRESH_TOKEN_USE_DAYS)

    for name, get_code, allowed in (("TOTP login", auth.headless_auth_code, auth.can_headless),
                                    ("browser login", auth.browser_auth_code, interactive)):
        if token or not allowed:
            continue
        try:
            tokens = auth.exchange_auth_code(get_code())
            token, how = tokens["access_token"], name
            if tokens.get("refresh_token"):
                store.update(refresh_token=tokens["refresh_token"], refresh_issued=today.isoformat())
                log.info("refresh token renewed (valid %d days)", REFRESH_TOKEN_DAYS)
        except (FyersAuthError, ConfigError) as ex:
            errors.append(f"{name}: {ex}")
            log.warning("%s failed: %s", name, ex)

    if not token:
        hint = "" if interactive else " Run once with --interactive to log in through the browser."
        raise FyersAuthError("could not get a Fyers access token: "
                             + " | ".join(errors or ["no login method configured (run --setup)"]) + "." + hint)
    profile = auth.validate(token)
    if not profile:
        raise FyersAuthError(f"Fyers did not accept the new token (via {how})")
    publish(env, cfg["app_id"], token, now_ist())
    who = (profile.get("fy_id") or profile.get("name")) if isinstance(profile, dict) else None
    log.info("new access token via %s for %s set in %s environment (FYERS_ACCESS_TOKEN %s)", how, who or "account",
             getattr(env, "scope", "user"), mask(token))
    return how


def write_status(ok: bool, how: str | None, error: str | None) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(json.dumps({"ok": ok, "via": how, "error": error,
                                           "at": now_ist().isoformat(timespec="seconds"), "version": __version__},
                                          indent=2), encoding="utf-8")
    except OSError:
        pass


# ------------------------------------------------------------------------------------------------------------------
# --setup / --status
# ------------------------------------------------------------------------------------------------------------------
def setup(env_user, store: SecretStore, visible: bool = False) -> None:
    print("NSEUIENGINE - Fyers credentials. Press Enter to keep the current value.")
    print("Secrets are typed/pasted " + ("VISIBLY - clear the screen afterwards (cls)." if visible else
                                         "hidden. Use --visible if pasting doesn't seem to work.") + "\n")
    sec = store.load()

    def ask(label, current, secret=False, default=None):
        shown = (_hint(current) if current else "<not set>") if secret else (current or default or "")
        prompt = f"{label} [{shown}]: " if shown else f"{label}: "
        while True:
            v = _clean(getpass.getpass(prompt) if secret and not visible else input(prompt))
            if secret and v and _doubled(v):
                print(f"    that looks entered twice ({len(v)} chars = the same text 2x). Enter it once.")
                continue
            if secret and not v and current and (_doubled(current) or _clean(current) != current):
                print("    the stored value is damaged (doubled or has control characters) - please enter it")
                continue
            if secret and v:
                print(f"    received {_hint(v)}")
            return v or current or default

    app_id = ask("Fyers App ID (myapi.fyers.in, e.g. ABCD1234-100)", read_any_env("FYERS_CLIENT_ID"))
    redirect = ask("Redirect URL (must match the app)", read_any_env("FYERS_REDIRECT_URI"), default=DEFAULT_REDIRECT)
    fy_id = ask("Fyers client/login ID (e.g. XY12345)", read_any_env("FYERS_ID"))
    secret_key = ask("App secret key", sec.get("secret_key") or os.environ.get("FYERS_SECRET_KEY"), secret=True)
    pin = ask("4-digit Fyers PIN", sec.get("pin"), secret=True)
    totp = ask("TOTP secret key (myaccount.fyers.in > Security > External 2FA TOTP)", sec.get("totp_secret"),
               secret=True)
    if pin and not (pin.isdigit() and len(pin) == 4):
        raise ConfigError(f"the Fyers PIN must be 4 digits (got {len(pin)} characters) - run --setup again")
    if totp and totp.isdigit():
        raise ConfigError("that is a 6-digit TOTP *code*. Enter the TOTP *key* Fyers shows when you enable External "
                          "2FA TOTP (letters A-Z and digits 2-7, usually 32 characters)")
    if secret_key and secret_key == (totp or "").replace(" ", "").upper():
        raise ConfigError("the app secret key and the TOTP key are identical - the secret key is the app's "
                          "Secret ID on myapi.fyers.in, not the TOTP key")
    if totp:
        import pyotp
        try:
            pyotp.TOTP(totp.replace(" ", "").upper()).now()
        except Exception as ex:
            raise ConfigError(f"the TOTP secret is not a valid base32 key ({ex})") from ex
    if not (app_id and secret_key):
        raise ConfigError("App ID and secret key are required")
    for n, v in (("FYERS_CLIENT_ID", app_id), ("FYERS_REDIRECT_URI", redirect), ("FYERS_ID", fy_id),
                 ("NSEUIENGINE_HOME", str(HOME))):
        if v:
            env_user.set(n, v)
    env_user.broadcast()
    sec.update({k: v for k, v in (("secret_key", secret_key), ("pin", pin),
                                  ("totp_secret", totp.replace(" ", "").upper() if totp else None)) if v})
    store.save(sec)
    print(f"\nSaved. Non-secret settings -> user environment; secrets -> {SECRETS_FILE} (DPAPI, this account only).")


def _clean(v: str) -> str:
    """Drop whitespace and control characters (a Ctrl+V at a hidden prompt arrives as \\x16)."""
    return "".join(c for c in (v or "") if ord(c) >= 32 and not c.isspace())


def _doubled(v: str) -> bool:
    n = len(v)
    return n >= 8 and n % 2 == 0 and v[: n // 2] == v[n // 2:]


def _hint(v: str | None) -> str:
    """Enough to recognise a secret without revealing it: length and first/last 2 characters."""
    if not v:
        return "<empty>"
    bad = [c for c in v if ord(c) < 32]
    extra = f", CONTAINS {len(bad)} CONTROL CHARACTER(S) - paste went wrong" if bad else ""
    if _doubled(v):
        extra += ", LOOKS ENTERED TWICE"
    return f"{len(v)} chars, {v[:2]}...{v[-2:]}{extra}" if len(v) > 6 else f"{len(v)} chars{extra}"


def diagnose() -> None:
    """Explain why a login fails, without sending any real login attempt (so it can't lock the account)."""
    import pyotp
    sec = SecretStore().load()
    app_id = read_any_env("FYERS_CLIENT_ID") or ""
    secret_key = sec.get("secret_key") or read_any_env("FYERS_SECRET_KEY") or ""
    totp_secret = sec.get("totp_secret") or read_any_env("FYERS_TOTP_SECRET") or ""
    pin = sec.get("pin") or read_any_env("FYERS_PIN") or ""
    print(f"App ID            {app_id or '<not set>'}")
    print(f"Fyers ID          {read_any_env('FYERS_ID') or '<not set>'}")
    print(f"Redirect URI      {read_any_env('FYERS_REDIRECT_URI') or DEFAULT_REDIRECT}")
    print(f"Secret key        {_hint(secret_key)}   (compare with myapi.fyers.in > your app > Secret ID)")
    pin_ok = pin.isdigit() and len(pin) == 4
    print(f"PIN               {len(pin)} chars -> {'OK' if pin_ok else 'WRONG: a Fyers PIN is 4 digits'}")
    print(f"TOTP secret       {_hint(totp_secret)}")

    # 1. clock: TOTP fails if the PC is more than ~30 s off
    try:
        r = requests.head("https://api-t1.fyers.in/", timeout=10)
        from email.utils import parsedate_to_datetime
        offset = (datetime.now(timezone.utc) - parsedate_to_datetime(r.headers["Date"])).total_seconds()
        verdict = "OK" if abs(offset) < 10 else "TOO FAR OFF - run: w32tm /resync  (as Administrator)"
        print(f"PC clock          {offset:+.1f} s vs Fyers server  -> {verdict}")
    except Exception as ex:
        print(f"PC clock          could not check ({ex})")

    # 2. TOTP: the code we would send right now; must match the authenticator app
    if totp_secret:
        try:
            t = pyotp.TOTP(totp_secret.replace(" ", "").upper())
            left = int(t.interval - time.time() % t.interval)
            print(f"TOTP code now     {t.now()}  (valid {left} more s)  -> must equal the code in your authenticator "
                  f"app for Fyers. If it doesn't, the stored TOTP secret is wrong: run --setup --visible")
        except Exception as ex:
            print(f"TOTP code now     cannot compute: {ex}")

    # 3. secret key: ask Fyers with a dummy auth code; the error says whether the app-id hash is accepted
    if app_id and secret_key:
        h = hashlib.sha256(f"{app_id}:{secret_key}".encode()).hexdigest()
        try:
            d = requests.post(f"{API}/validate-authcode", json={"grant_type": "authorization_code", "appIdHash": h,
                                                                "code": "diagnose"}, timeout=15).json()
            m = _msg(d)
            if "hash" in m.lower():
                v = "REJECTED: secret key does not match this App ID. Copy the Secret ID again and run --setup --visible"
            else:
                v = ("inconclusive - Fyers checks the auth code before the secret key, so only a real login "
                     "(--force --interactive) proves the key")
            print(f"Secret key check  Fyers says '{m}'  -> {v}")
        except Exception as ex:
            print(f"Secret key check  could not reach Fyers ({ex})")


def status(env) -> None:
    sec = {}
    try:
        sec = SecretStore().load()
    except ConfigError as ex:
        print(f"secrets: {ex}")
    print(f"NSEUIENGINE_HOME      {HOME}")
    for n in ("FYERS_CLIENT_ID", "FYERS_ID", "FYERS_REDIRECT_URI"):
        print(f"{n:<22}{read_any_env(n) or '<not set>'}")
    for n in TOKEN_VARS:
        v = env.get(n)
        print(f"{n:<22}{mask(v) if n in SENSITIVE_VARS else (v or '<not set>')}")
    for k in ("secret_key", "pin", "totp_secret", "refresh_token"):
        print(f"secret {k:<15}{'set' if sec.get(k) else 'not set'}")
    if sec.get("refresh_issued"):
        print(f"refresh token issued  {sec['refresh_issued']}")
    if STATUS_FILE.exists():
        print(f"last run              {STATUS_FILE.read_text(encoding='utf-8').strip()}")


# ------------------------------------------------------------------------------------------------------------------
def setup_logging(verbose: bool) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    fh = logging.handlers.RotatingFileHandler(LOG_DIR / "fyers_token.log", maxBytes=1_000_000, backupCount=5,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stdout is not None:                       # pythonw.exe has no console
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        log.addHandler(sh)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="NSEUIENGINE: keep today's Fyers access token in the environment")
    p.add_argument("--force", action="store_true", help="get a new token even if today's is valid")
    p.add_argument("--interactive", action="store_true", help="allow the browser login as a last resort")
    p.add_argument("--setup", action="store_true", help="enter / change credentials")
    p.add_argument("--status", action="store_true", help="show what is configured (tokens masked)")
    p.add_argument("--diagnose", action="store_true", help="check clock, TOTP code and secret key (no login attempt)")
    p.add_argument("--visible", action="store_true", help="with --setup: show secrets while typing/pasting")
    p.add_argument("--scope", choices=("user", "machine"), default="user",
                   help="where to write the token variables (machine = all users, needs elevation)")
    p.add_argument("--proxy", action="store_true", help="send login calls through %%STATICIP_PROXY%%")
    p.add_argument("--retries", type=int, default=3, help="attempts on network errors (default 3)")
    p.add_argument("--retry-wait", type=int, default=120, help="seconds between attempts (default 120)")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)

    if os.name != "nt":
        print("NSEUIENGINE token manager runs on Windows only", file=sys.stderr)
        return 2
    setup_logging(a.verbose)
    try:
        if a.setup:
            setup(WinEnv("user"), SecretStore(), visible=a.visible)
            return 0
        if a.diagnose:
            diagnose()
            return 0
        env = WinEnv(a.scope)
        if a.status:
            status(env)
            return 0
        proxy = read_any_env("STATICIP_PROXY") if a.proxy else None
        if a.proxy and not proxy:
            raise ConfigError("--proxy given but STATICIP_PROXY is not set")
        log.info("NSEUIENGINE fyers token manager %s start (scope=%s%s)", __version__, a.scope,
                 ", via static-IP proxy" if proxy else "")
        for attempt in range(1, max(1, a.retries) + 1):
            try:
                how = ensure_token(env, SecretStore(), force=a.force, interactive=a.interactive, proxy=proxy)
                write_status(True, how, None)
                return 0
            except requests.RequestException as ex:         # network only - safe to retry
                log.warning("network error (attempt %d/%d): %s", attempt, a.retries, ex)
                if attempt >= a.retries:
                    raise
                time.sleep(a.retry_wait)
    except (FyersAuthError, ConfigError, requests.RequestException) as ex:
        log.error("%s", ex)
        write_status(False, None, str(ex))
        return 1
    except Exception as ex:                                  # never die silently under pythonw
        log.exception("unexpected error: %s", ex)
        write_status(False, None, f"unexpected: {ex}")
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
