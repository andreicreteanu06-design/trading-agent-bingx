"""Risk-based position sizing for leveraged crypto futures.

Adapted from claude-trading-skills' position-sizer (originally for long stock
trades) to work in USDT margin terms with leverage, for a BingX futures bot.

Two sizing modes:
  - Fixed fractional: risk a fixed % of account per trade, sized off the
    stop-loss distance.
  - Kelly criterion: size off historical win-rate / win-loss ratio, using
    half-Kelly (full Kelly is too aggressive for leveraged futures).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SizingParams:
    account_balance_usdt: float
    entry_price: float
    stop_price: float
    risk_pct: float = 1.0          # % of account risked per trade
    leverage: float = 1.0          # exchange leverage applied to the position
    max_position_pct: float = 20.0 # cap: position notional as % of account
    win_rate: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None


def validate(params: SizingParams) -> None:
    if params.account_balance_usdt <= 0:
        raise ValueError("account_balance_usdt must be positive")
    if params.entry_price <= 0 or params.stop_price <= 0:
        raise ValueError("entry_price and stop_price must be positive")
    if params.entry_price == params.stop_price:
        raise ValueError("stop_price must differ from entry_price")
    if not (0 < params.risk_pct <= 100):
        raise ValueError("risk_pct must be between 0 and 100")
    if params.leverage <= 0:
        raise ValueError("leverage must be positive")
    if params.win_rate is not None and not (0 < params.win_rate <= 1.0):
        raise ValueError("win_rate must be between 0 (exclusive) and 1.0")


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Half-Kelly % of account to risk, floored at 0 on negative expectancy."""
    if avg_loss <= 0:
        raise ValueError("avg_loss must be positive")
    r = avg_win / avg_loss
    full_kelly = win_rate - (1 - win_rate) / r
    return max(0.0, full_kelly) * 100 / 2  # half-Kelly, as a percentage


def calculate_position(params: SizingParams) -> dict:
    """Returns contract quantity, notional size, and margin required."""
    validate(params)

    risk_per_unit = abs(params.entry_price - params.stop_price)
    is_long = params.stop_price < params.entry_price

    if params.win_rate is not None and params.avg_win and params.avg_loss:
        risk_pct = kelly_fraction(params.win_rate, params.avg_win, params.avg_loss)
        sizing_method = "half_kelly"
    else:
        risk_pct = params.risk_pct
        sizing_method = "fixed_fractional"

    dollar_risk = params.account_balance_usdt * risk_pct / 100
    quantity = dollar_risk / risk_per_unit

    notional = quantity * params.entry_price
    max_notional = params.account_balance_usdt * params.max_position_pct / 100
    binding_constraint = None
    if notional > max_notional:
        quantity = max_notional / params.entry_price
        notional = max_notional
        binding_constraint = "max_position_pct"

    margin_required = notional / params.leverage

    return {
        "side": "long" if is_long else "short",
        "sizing_method": sizing_method,
        "risk_pct_used": round(risk_pct, 3),
        "dollar_risk": round(dollar_risk, 2),
        "quantity": quantity,
        "notional_usdt": round(notional, 2),
        "margin_required_usdt": round(margin_required, 2),
        "leverage": params.leverage,
        "binding_constraint": binding_constraint,
        "liquidation_warning": (
            "margin_required exceeds 80% of account balance — reduce leverage or size"
            if margin_required > params.account_balance_usdt * 0.8
            else None
        ),
    }


if __name__ == "__main__":
    example = SizingParams(
        account_balance_usdt=1000.0,
        entry_price=65000.0,
        stop_price=63700.0,
        risk_pct=1.0,
        leverage=5.0,
        max_position_pct=20.0,
    )
    import json

    print(json.dumps(calculate_position(example), indent=2))
