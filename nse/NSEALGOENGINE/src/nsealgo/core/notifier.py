"""Telegram notifications (optional). Failures never interrupt trading."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import logging

log = logging.getLogger("nsealgo.notify")


class Notifier:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.token, self.chat = cfg.get("telegram_token") or "", cfg.get("telegram_chat_id") or ""

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat)

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            import requests
            requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                          data={"chat_id": self.chat, "text": text[:4000]}, timeout=5)
        except Exception as ex:
            log.debug("telegram failed: %s", ex)
