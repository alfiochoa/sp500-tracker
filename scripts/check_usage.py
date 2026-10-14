"""Print the account-wide Twelve Data usage (free endpoint, spends no credits)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sp500.guard import ACCOUNT_CAP, UsageUnavailable, fetch_usage

try:
    u = fetch_usage(os.environ.get("TWELVE_DATA_API_KEY", ""))
except UsageUnavailable as e:
    print(f"::warning::api_usage unavailable ({e}); automatic jobs would fail closed")
    raise SystemExit(0)
print(f"twelvedata account usage: {u['daily_usage']}/{u['plan_daily_limit']} today, {u['current_usage']} this minute; "
      f"automatic spending stops at {ACCOUNT_CAP}")
