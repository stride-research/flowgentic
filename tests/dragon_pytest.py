"""pytest as a script, since ``dragon`` starts a Python script:

    dragon tests/dragon_pytest.py -m dragon tests

runs the Dragon suite (see ``tests/_dragon.py``).
"""

import sys

import pytest

if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv[1:]))
