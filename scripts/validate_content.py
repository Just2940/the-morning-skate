#!/usr/bin/env python3
"""
validate_content.py - Section 0 anti-regression gatekeeper for The Morning Skate.

Runs every rule from SKILL.md Section 0 against data.json and index.html.
Exits 0 if clean, 1 if any ERROR triggers. WARNINGS are printed but don't block.

Usage:
    python scripts/validate_content.py
    python scripts/validate_content.py --data data.json --html index.html
    python scripts/validate_content.py --skip-network   # skip HTTP HEAD checks (CI fallback)
    python scripts/validate_content.py --strict         # upgrade WARNINGS to ERRORS

This script is the enforcement layer that makes daily updates trustworthy.
The skill (SKILL.md) documents the rules; this script PROVES them.
Every rule maps 1:1 to a subsection in Section 0 (cited in comments).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# ------------------------------------------------------------------------- #
# Reporter - collects issues and prints a final readable report             #
# ------------------------------------------------------------------------- #


class Reporter:
    def __init__(self, strict: bool = False) -> None:
        self.errors: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []
        self.passed: list[str] = []
        self.strict = strict

    def error(self, rule: str, msg: str) -> None:
        self.errors.append((rule, msg))

    def warn(self, rule: str, msg: str) -> None:
        if self.strict:
            self.errors.append((rule, f"(was WARN) {msg}"))
        else:
            self.warnings.append((rule, msg))

    def ok(self, rule: str) -> None:
        self.passed.append(rule)

    def print_report(self) -> int:
        print("=" * 72)
        print("  The Morning Skate - Section 0 Validation Report")
        print("=" * 72)
        for rule in self.passed:
            print(f"  [PASS] {rule}")
        for rule, msg in self.warnings:
            print(f"  [WARN] {rule}: {msg}")
        for rule, msg in self.errors:
            print(f"  [FAIL] {rule}: {msg}")
        print("-" * 72)
        print(
            f"  {len(self.passed)} passed, {len(self.warnings)} warnings, "
            f"{len(self.errors)} errors"
        )
        print("=" * 72)
        return 1 if self.errors else 0
