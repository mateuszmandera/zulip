from django.conf import settings
from django.core.mail import EmailMultiAlternatives, SafeMIMEMultipart

from zerver.lib.email_helpers import format_to
from zerver.lib.logging_util import log_to_file
from zerver.models import UserProfile, UserPGP

from copy import deepcopy
from typing import Dict, Optional, List, DefaultDict, Callable, Any

class PGPKeyNotFound(Exception):
    pass

# To have the email correctly formatted according to PGP/MIME (RFC3156),
# we need full control over the various MIME parts and headers,
# that we don't have through EmailMultiAlternatives.
# When sending an email, Django invokes the .message() method of EmailMessage
# which converts it and returns an object of type used by the python "email"
# library. This is the stage at which we have the desired level of control.
# (See django.core.mail.message for details.)
# Thus, encryption and signing need to happen by overriding .message().
class PGPEmailMessage(EmailMultiAlternatives):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._encrypted = False
        self._signed = False
        self.public_keys = []  # type: List[str]

    def sign(self) -> None:
        self._signed = True

    def encrypt(self, public_keys: List[str], sign: bool=False) -> None:
        self._encrypted = True
        self._signed = sign
        self.public_keys = public_keys

    # super().message() will return the original email message, which we can
    # then sign/encrypt and set apprioprate PGP/MIME headers. The object
    # returned by this method is the MIME message that will be sent to the
    # recipients.
    def message(self) -> SafeMIMEMultipart:
        original = super().message()
        if self._encrypted or self._signed:
            raise NotImplementedError()

        return original

def pgp_sign_and_encrypt(message: PGPEmailMessage, to_users: List[UserProfile],
                         force_single_message: bool=False) -> List[PGPEmailMessage]:
    public_keys = {}  # type: Dict[str, str]
    want_signatures = []  # type: List[str]

    to_emails = [format_to(to_user) for to_user in to_users]
    for to_user in to_users:
        if settings.ENABLE_EMAIL_ENCRYPTION and to_user.want_encrypted_emails:
            try:
                user_pgp = UserPGP.objects.get(user_profile_id=to_user.id)
                public_keys[format_to(to_user)] = user_pgp.public_key
            except UserPGP.DoesNotExist:  # Should never happen
                raise PGPKeyNotFound('User (id %s) has want_encrypted_emails=True, '
                                     'but no public key in the database' % (to_user.id))
        if settings.ENABLE_EMAIL_SIGNATURES and to_user.want_signed_emails:
            want_signatures.append(format_to(to_user))

    return _sign_and_encrypt(message, to_emails, public_keys, want_signatures,
                             force_single_message)

def _sign_and_encrypt(message: PGPEmailMessage, to_emails: List[str],
                      public_keys: Dict[str, str], want_signatures: List[str],
                      force_single_message: bool=False) -> List[PGPEmailMessage]:
    # Addressees who don't want encryption nor signatures
    basic_addressees = [to for to in to_emails if to not in public_keys
                        and to not in want_signatures]
    # Addressees who only want a signature, without encryption
    signature_addressees = [to for to in want_signatures if to not in public_keys]
    # Addresees who want encryption, and perhaps a signature
    encrypt_addressees = list(public_keys.keys())

    basic_message = deepcopy(message)
    basic_message.to = basic_addressees

    if force_single_message:
        if not basic_addressees and not signature_addressees:
            # Every addressee wants encryption
            encrypted_message = deepcopy(basic_message)
            # TODO: all or any?
            sign = all(to in want_signatures for to in encrypt_addressees)
            encrypted_message.to = to_emails
            encrypted_message.encrypt(list(public_keys.values()), sign)
            return [encrypted_message]
        else:
            # We can't encrypt
            # TODO: all or any?
            if all(to in want_signatures for to in to_emails):
                signed_message = deepcopy(message)
                signed_message.to = to_emails
                signed_message.sign()
                return [signed_message]
            else:
                basic_message.to = to_emails
                return [basic_message]

    prepared_messages = [basic_message] if basic_addressees else []

    if signature_addressees:
        signed_message = deepcopy(message)
        signed_message.to = signature_addressees
        signed_message.sign()
        prepared_messages.append(signed_message)

    for to in encrypt_addressees:
        encrypted_message = deepcopy(basic_message)
        encrypted_message.to = [to]
        encrypted_message.encrypt([public_keys[to]], to in want_signatures)
        prepared_messages.append(encrypted_message)

    return prepared_messages
