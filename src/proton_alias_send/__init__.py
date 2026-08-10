"""Create Proton Pass / SimpleLogin reverse aliases from the command line."""

__version__ = "0.1.0"

from proton_alias_send.cli import create_contact, find_alias_id, main

__all__ = ["__version__", "create_contact", "find_alias_id", "main"]
