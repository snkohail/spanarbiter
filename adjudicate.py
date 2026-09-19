#!/usr/bin/env python3
"""Launcher, so the tool runs straight from a clone with nothing installed.

The package lives under src/ (the layout that stops tests from accidentally importing the
working copy instead of the installed one). That means `python3 -m adjudicator` only works once
the package is on the path. This script puts it there, so a fresh clone runs immediately:

    python3 adjudicate.py serve --a-dir A --b-dir B --out OUT

After `pip install -e .` the equivalent commands are `python3 -m adjudicator ...` or the
`adjudicator` console script.
"""
import os
import sys

# Checked before anything else is imported, so an old interpreter gets a sentence rather than a
# SyntaxError from a module it could never have parsed.
if sys.version_info < (3, 9):
    sys.exit("SpanArbiter needs Python 3.9 or newer. This is Python %d.%d at %s."
             % (sys.version_info[0], sys.version_info[1], sys.executable))

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from adjudicator.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
