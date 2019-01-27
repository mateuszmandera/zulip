from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from zerver.lib.email_helpers import format_to
from zerver.lib.logging_util import log_to_file
from zerver.models import UserProfile, UserPGP

from copy import deepcopy
from typing import Dict, Optional, List, DefaultDict, Callable

import logging

logger = logging.getLogger('zulip.pgp')
log_to_file(logger, settings.EMAIL_LOG_PATH)

def pgp_sign_and_encrypt(message: EmailMultiAlternatives, to_users: List[UserProfile],
                         force_single_message: bool=False) -> List[EmailMultiAlternatives]:
    public_keys = {}  # type: Dict[str, str]
    want_signatures = []  # type: List[str]

    to_emails = [format_to(to_user) for to_user in to_users]
    for to_user in to_users:
        if settings.ENABLE_EMAIL_ENCRYPTION and to_user.want_encrypted_emails:
            try:
                user_pgp = UserPGP.objects.get(user_profile_id=to_user.id)
                public_keys[format_to(to_user)] = user_pgp.public_key
            except UserPGP.DoesNotExist:  # Should never happen
                logger.error('User (id %s) has want_encrypted_emails=True, '
                             'but no public key in the database' % (to_user.id))
        if settings.ENABLE_EMAIL_SIGNATURES and to_user.want_signed_emails:
            want_signatures.append(format_to(to_user))

    return _sign_and_encrypt(message, to_emails, public_keys, want_signatures,
                             force_single_message)

def _sign_and_encrypt(message: EmailMultiAlternatives, to_emails: List[str],
                      public_keys: Dict[str, str], want_signatures: List[str],
                      force_single_message: bool=False) -> List[EmailMultiAlternatives]:
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
            encrypt_message(encrypted_message, list(public_keys.values()), sign)
            encrypted_message.to = to_emails
            return [encrypted_message]
        else:
            # We can't encrypt
            # TODO: all or any?
            if all(to in want_signatures for to in to_emails):
                signed_message = deepcopy(message)
                signed_message.to = to_emails
                sign_message(signed_message)
                return [signed_message]
            else:
                basic_message.to = to_emails
                return [basic_message]

    prepared_messages = [basic_message] if basic_addressees else []

    if signature_addressees:
        signed_message = deepcopy(message)
        signed_message.to = signature_addressees
        sign_message(signed_message)
        prepared_messages.append(signed_message)

    for to in encrypt_addressees:
        encrypted_message = deepcopy(basic_message)
        encrypt_message(encrypted_message, [public_keys[to]], to in want_signatures)
        encrypted_message.to = to
        prepared_messages.append(encrypted_message)

    return prepared_messages

def sign_message(message: EmailMultiAlternatives) -> None:
    raise NotImplementedError()

def encrypt_message(message: EmailMultiAlternatives, public_keys: List[str],
                    sign: bool=False) -> None:
    raise NotImplementedError()
