"""Execution module: turn an approved equity Decision into a paper-mode order + Position.

Money-path guardrails (see ``service.py``): Alpaca **paper only**, equities only,
crypto/Binance execution is refused, risk gate is mandatory, and execution is
idempotent (a Decision already linked to a Position is never re-executed).
"""
