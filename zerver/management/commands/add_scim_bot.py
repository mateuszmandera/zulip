from argparse import ArgumentParser
from typing import Any

from zerver.lib.actions import do_create_user
from zerver.lib.management import ZulipBaseCommand
from zerver.models import UserProfile


class Command(ZulipBaseCommand):
    help = """Change the email address for a user."""

    def add_arguments(self, parser: ArgumentParser) -> None:
        self.add_realm_args(parser)
        parser.add_argument("email", help="email address of the bot")

    def handle(self, *args: Any, **options: Any) -> None:
        bot_email = options["email"]
        realm = self.get_realm(options)
        assert realm

        do_create_user(
            bot_email, None, realm, "SCIM Bot", bot_type=UserProfile.DEFAULT_BOT, acting_user=None
        )
