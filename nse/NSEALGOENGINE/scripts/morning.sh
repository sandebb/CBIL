#!/usr/bin/env bash
# Daily routine before 09:00 IST: log in (SEBI daily 2FA), verify the adapter, then start the forward test.
set -euo pipefail
BROKER="${1:-zerodha}"
cd "$(dirname "$0")/.."
nsealgo login "$BROKER"
nsealgo smoke-test "$BROKER" --symbol SBIN
exec nsealgo forwardtest --data-broker "$BROKER"
