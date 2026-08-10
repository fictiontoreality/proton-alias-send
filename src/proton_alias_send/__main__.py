"""Entry point for `python -m proton_alias_send`."""

import sys

from proton_alias_send.cli import main

if __name__ == "__main__":
    sys.exit(main())
