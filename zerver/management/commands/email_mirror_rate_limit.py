from twisted.internet.endpoints import UNIXServerEndpoint, TCP4ServerEndpoint
from twisted.internet import reactor
from twisted.internet.protocol import Factory
from twisted.logger import eventsFromJSONLogFile, textFileLogObserver, globalLogBeginner
from twisted.protocols.basic import LineReceiver

from django.conf import settings
from django.core.management.base import BaseCommand

from typing import Any, Dict

from zerver.lib.email_mirror import extract_and_validate, is_missed_message_address, \
    rate_limit_mirror_by_realm, ZulipEmailForwardError
from zerver.lib.rate_limiter import RateLimited

import sys
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    def handle(self, *args: Any, **options: Any) -> None:
        endpoint = UNIXServerEndpoint(reactor, "/tmp/zulip_email_mirror_rate_limit_daemon")
        #endpoint = TCP4ServerEndpoint(reactor, 9091)
        endpoint.listen(PostfixPolicyFactory())

        observer = textFileLogObserver(sys.stdout)
        globalLogBeginner.beginLoggingTo([observer])

        reactor.run()

class PostfixPolicyProtocol(LineReceiver):
    """
    This class handles communication with Postfix:
    http://www.postfix.org/SMTPD_POLICY_README.html
    """
    delimiter = b'\n\n'

    def lineReceived(self, input_line: bytes):
        multi_line = input_line.decode()
        input_dict = {
            k: v for k, v in (
                line.split('=', 1) for line in multi_line.split('\n')
            )
        }
        self.gotPostfixRequest(input_dict)

    def gotPostfixRequest(self, input_dict: Dict[str, str]) -> None:
        if 'recipient' not in input_dict:
            self.sendPostfixAction("DUNNO")
            return

        rcpt_to = input_dict['recipient']
        print("hey {}".format(rcpt_to))
        if not is_missed_message_address(rcpt_to):
            # Missed message addresses are one-time use, so we don't need
            # to worry about emails to them resulting in message spam.
            try:
                recipient_realm = extract_and_validate(rcpt_to)[0].realm
                rate_limit_mirror_by_realm(recipient_realm)
                print(recipient_realm.string_id)
            except RateLimited:
                logger.warning("Rejecting an email from: %s "
                               "to realm: %s - rate limited."
                               % (input_dict.get('sender', None), recipient_realm.name))
                self.sendPostfixAction("REJECT")
                return
            except ZulipEmailForwardError as e:
                logger.warning("%s: Rejecting email from: %s to: %s."
                               % (str(e), input_dict.get('sender', None), rcpt_to))
                self.sendPostfixAction("REJECT")
                return

        # It's not this daemon's role to strictly ACCEPT.
        # It only decides not to REJECT, so if we got to this line,
        # we just respond with DUNNO.
        self.sendPostfixAction("DUNNO")

    def sendPostfixAction(self, response: str):
        # Sends response followed by an empty line,
        # as expected by Postfix.
        self.sendLine('action={}'.format(response).encode())

# denmark.0bf86e5c919249a23efb0f84ed10e303.show-sender@zulipdev.com 

class PostfixPolicyFactory(Factory):
    protocol = PostfixPolicyProtocol
