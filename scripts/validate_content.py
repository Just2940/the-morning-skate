#!/usr/bin/env python3
"""
validate_content.py — Section 0 anti-regression gatekeeper for The Morning Skate.

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

SMOKETEST_PLACEHOLDER