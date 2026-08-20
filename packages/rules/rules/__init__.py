"""Deterministic legal rules engine for Every Front.

Working agreement §2.1: all deadline math, eligibility math, NCCI checks, and
front-selection logic live here as pure, unit-tested Python. LLMs classify,
extract, and draft -- they never compute a deadline. There are ZERO LLM calls
in this package and there must never be any.

Agreement §2.2: every legal rule cites its source in the docstring --
regulation section plus effective date.
"""
