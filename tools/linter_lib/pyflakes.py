import argparse

from typing import List

from zulint.linters import run_pyflakes


def check_pyflakes(files, options):
    # type: (List[str], argparse.Namespace) -> bool
    suppress_patterns = [
        ("scripts/lib/pythonrc.py", "imported but unused"),
        # Intentionally imported by zerver/lib/webhooks/common.py
        ('', "'zerver.lib.exceptions.UnexpectedWebhookEventType' imported but unused"),


        # Our ipython startup pythonrc file intentionally imports *
        ("scripts/lib/pythonrc.py",
         " import *' used; unable to detect undefined names"),

        # Special dev_settings.py import
        ('', "from .prod_settings_template import *"),

        ("settings.py", "settings import *' used; unable to detect undefined names"),
        ("settings.py", "may be undefined, or defined from star imports"),

        # Sphinx adds `tags` specially to the environment when running conf.py.
        ("docs/conf.py", "undefined name 'tags'"),

        # decorator.py needs to use type declaration Optional['Type[RateLimiterBackend']], which
        # doesn't get detected as "use" of these objects by pyflakes. The '' can't be removed from
        # the declaration until we drop support for python 3.5, as Optional[Type[...]] causes an exception.
        ("zerver/decorator.py", "'zerver.lib.rate_limiter.RateLimiterBackend' imported but unused"),
        ("zerver/decorator.py", "'typing.Type' imported but unused"),
    ]
    if options.full:
        suppress_patterns = []
    return run_pyflakes(files, options, suppress_patterns)
