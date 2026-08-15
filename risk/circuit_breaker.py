"""Account-level drawdown circuit breaker for an automated futures bot.

Adapted from claude-trading-skills' drawdown-circuit-breaker. The original
reads YAML "thesis" files written by a manual journaling workflow; this
version reads a simple local trade log (JSONL) that the bot itself appends
one line to after every closed trade:

    {"pnl": -42.10, "closed_at": "2026-08-14T10:15:00+00:00"}

Call check_circuit_breaker() before every new order. If it returns anything
other than TRADING_ALLOWED, do not open a new position.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass
class CircuitConfig:
    max_daily_loss_pct: float = 2.0
    losing_streak_n: int = 3
    cooldown_hours: float = 12.0
    weekly_drawdown_pct: float = 5.0
    monthly_drawdown_pct: float = 10.0


def _load_trade_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    trades = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            trades.append(json.loads(line))
    trades.sort(key=lambda t: t["closed_at"])
    return trades


def append_trade(path: Path, pnl: float, closed_at: datetime | None = None) -> None:
    """Call this from the bot right after a position closes."""
    entry = {
        "pnl": pnl,
        "closed_at": (closed_at or datetime.now(timezone.utc)).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _sum_since(trades: list[dict], since: datetime) -> float:
    return sum(
        t["pnl"] for t in trades if datetime.fromisoformat(t["closed_at"]) >= since
    )


def _consecutive_losses(trades: list[dict]) -> tuple[int, datetime | None]:
    count = 0
    last_loss_at = None
    for t in reversed(trades):
        if t["pnl"] >= 0:
            break
        count += 1
        if last_loss_at is None:
            last_loss_at = datetime.fromisoformat(t["closed_at"])
    return count, last_loss_at


def check_circuit_breaker(
    trade_log_path: Path,
    account_balance_usdt: float,
    config: CircuitConfig | None = None,
    now: datetime | None = None,
) -> dict:
    config = config or CircuitConfig()
    now = now or datetime.now(timezone.utc)
    trades = _load_trade_log(trade_log_path)

    day_start = now - timedelta(hours=24)
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    pnl_today = _sum_since(trades, day_start)
    pnl_week = _sum_since(trades, week_start)
    pnl_month = _sum_since(trades, month_start)
    consecutive_losses, last_loss_at = _consecutive_losses(trades)

    triggered = []
    recommendation = "TRADING_ALLOWED"

    daily_threshold = account_balance_usdt * config.max_daily_loss_pct / 100
    if pnl_today <= -daily_threshold:
        triggered.append(f"max_daily_loss: {pnl_today:.2f} <= -{daily_threshold:.2f}")
        recommendation = "HALTED"

    weekly_threshold = account_balance_usdt * config.weekly_drawdown_pct / 100
    if pnl_week <= -weekly_threshold:
        triggered.append(f"weekly_drawdown: {pnl_week:.2f} <= -{weekly_threshold:.2f}")
        recommendation = "HALTED"

    monthly_threshold = account_balance_usdt * config.monthly_drawdown_pct / 100
    if pnl_month <= -monthly_threshold:
        triggered.append(f"monthly_drawdown: {pnl_month:.2f} <= -{monthly_threshold:.2f}")
        recommendation = "HALTED"

    if consecutive_losses >= config.losing_streak_n and last_loss_at is not None:
        cooldown_until = last_loss_at + timedelta(hours=config.cooldown_hours)
        if now < cooldown_until:
            triggered.append(
                f"losing_streak_cooldown: {consecutive_losses} losses in a row, "
                f"cooldown until {cooldown_until.isoformat()}"
            )
            if recommendation == "TRADING_ALLOWED":
                recommendation = "COOLDOWN"

    return {
        "recommendation": recommendation,
        "checked_at": now.isoformat(),
        "metrics": {
            "pnl_today": round(pnl_today, 2),
            "pnl_7d": round(pnl_week, 2),
            "pnl_30d": round(pnl_month, 2),
            "consecutive_losses": consecutive_losses,
        },
        "triggered_rules": triggered,
        "config": asdict(config),
    }


if __name__ == "__main__":
    result = check_circuit_breaker(
        trade_log_path=Path("state/trade_log.jsonl"),
        account_balance_usdt=1000.0,
    )
    print(json.dumps(result, indent=2))
