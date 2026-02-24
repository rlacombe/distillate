"""Allow ``python -m distillate`` to work as an entry point."""

from distillate.main import _main_wrapper

_main_wrapper()
