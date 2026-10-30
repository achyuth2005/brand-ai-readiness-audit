#!/usr/bin/env bash
# Run the offline test suite. No network access is required or used.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 tests/sync_check_tables.py --check
python3 -m unittest discover -s tests -p "test_*.py" -t tests "$@"
