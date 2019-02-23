from django.conf import settings
from django.core import mail
from django.core.mail import get_connection
from django.core.mail.message import SafeMIMEText, SafeMIMEMultipart
from django.test import override_settings

from email import message_from_bytes
from email.message import Message

from gnupg import GPG

from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.pgp import (
    gpg_encrypt_content,
    gpg_decrypt_content,
    gpg_sign_content,
    gpg_verify_signature,
    pgp_sign_and_encrypt,
    PGPEmailMessage,
    PGPKeyNotFound,
    PGPSignatureFailed,
    PGPEncryptionFailed
)

from zerver.models import UserProfile, UserPGP

from shutil import rmtree
from typing import List, Dict

import os

def setup_testing_keyring(server_key: str) -> None:
    gpg = GPG(gnupghome=settings.GPG_HOME)
    result = gpg.import_keys(server_key)
    f = open(os.path.join(settings.GPG_HOME, "gpg.conf"), "w")
    f.write("default-key " + result.fingerprints[0])
    f.close()

def destroy_testing_keyring() -> None:
    rmtree(settings.GPG_HOME)

class TestPGP(ZulipTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        gpg = GPG(gnupghome=settings.GPG_HOME)
        cls.gpg = gpg

        # ugly hack to have access to helpers inside this class method
        helper = ZulipTestCase()

        server_key = helper.fixture_data('server_key.asc', type='email/pgp')
        setup_testing_keyring(server_key)

        hamlet_key = helper.fixture_data('hamlet_key.asc', type='email/pgp')
        othello_key = helper.fixture_data('othello_key.asc', type='email/pgp')
        iago_key = helper.fixture_data('iago_key.asc', type='email/pgp')
        gpg.import_keys('\n'.join([hamlet_key, othello_key, iago_key]))

        user_profile = helper.example_user('hamlet')
        user_pgp = UserPGP(user_profile=user_profile,
                           public_key=str(gpg.export_keys(helper.example_email('hamlet'))))
        user_pgp.save()

        user_profile = helper.example_user('othello')
        user_pgp = UserPGP(user_profile=user_profile,
                           public_key=str(gpg.export_keys(helper.example_email('othello'))))
        user_pgp.save()

        user_profile = helper.example_user('iago')
        user_pgp = UserPGP(user_profile=user_profile,
                           public_key=str(gpg.export_keys(helper.example_email('iago'))))
        user_pgp.save()

    @classmethod
    def tearDownClass(cls) -> None:
        UserPGP.objects.all().delete()
        destroy_testing_keyring()

    def check_signed_msg_structure(self, message: Message) -> None:
        self.assertTrue(message.is_multipart())
        self.assertEqual(message.get_content_subtype(), 'signed')
        self.assertEqual(message.get_param('protocol'), 'application/pgp-signature')

        parts = message.get_payload()
        assert isinstance(parts, List)
        assert isinstance(parts[1], Message)
        self.assertEqual(parts[1].get_content_type(), 'application/pgp-signature')
        signature = str(parts[1].get_payload())
        assert isinstance(parts[0], SafeMIMEText) or isinstance(parts[0], SafeMIMEMultipart)
        verify = gpg_verify_signature(signature, parts[0].as_bytes(linesep='\r\n'))
        self.assertTrue(verify)

    def check_encrypted_msg_structure(self, message: Message, body: str,
                                      signed: bool=False) -> None:
        self.assertTrue(message.is_multipart())
        self.assertEqual(message.get_content_subtype(), 'encrypted')
        self.assertEqual(message.get_param('protocol'), 'application/pgp-encrypted')

        parts = message.get_payload()
        assert isinstance(parts, List)
        assert isinstance(parts[0], Message)
        assert isinstance(parts[1], Message)
        self.assertEqual(parts[0].get_content_type(), 'application/pgp-encrypted')
        self.assertEqual(parts[0].get_payload(), 'Version: 1')
        self.assertEqual(parts[1].get_content_type(), 'application/octet-stream')

        encrypted = str(parts[1].get_payload())
        decrypted, is_signed = gpg_decrypt_content(encrypted)
        self.assertEqual(signed, is_signed)
        msg = message_from_bytes(decrypted)

        # we need to ensure uniform convention for line endings
        payload = str(msg.get_payload()).replace('\r\n', '\n')
        expected_body = body.replace('\r\n', '\n')
        self.assertEqual(payload, expected_body)

    def test_gpg_signatures(self) -> None:
        data = "test message".encode()
        signature = gpg_sign_content(data)
        verify = gpg_verify_signature(signature, data)
        self.assertTrue(verify)

    def test_gpg_encryption(self) -> None:
        data = "test message".encode()
        hamlet_key = self.fixture_data('hamlet_key.asc', type='email/pgp')
        self.gpg.import_keys(hamlet_key)
        hamlet_pubkey = self.gpg.export_keys(self.example_email('hamlet'))

        encrypted = gpg_encrypt_content(data, [hamlet_pubkey])
        decrypted, signed = gpg_decrypt_content(encrypted)
        self.assertFalse(signed)
        self.assertEqual(data, decrypted)

        encrypted = gpg_encrypt_content(data, [hamlet_pubkey], sign=True)
        decrypted, signed = gpg_decrypt_content(encrypted)
        self.assertTrue(signed)
        self.assertEqual(data, decrypted)

    @override_settings(GPG_HOME=os.path.join(settings.DEPLOY_ROOT, '.emptygpg'))
    def test_gpg_sign_wrong_gpghome(self) -> None:
        with self.assertRaises(PGPSignatureFailed):
            gpg_sign_content(b'test')

        rmtree(os.path.join(settings.DEPLOY_ROOT, '.emptygpg'))

    @override_settings(GPG_HOME=os.path.join(settings.DEPLOY_ROOT, '.emptygpg'))
    def test_pgp_encrypt_wrong_gpghome(self) -> None:
        hamlet_key = self.fixture_data('hamlet_key.asc', type='email/pgp')
        self.gpg.import_keys(hamlet_key)
        hamlet_pubkey = self.gpg.export_keys(self.example_email('hamlet'))
        with self.assertRaises(PGPEncryptionFailed):
            gpg_encrypt_content(b'test', [hamlet_pubkey], sign=True)

        rmtree(os.path.join(settings.DEPLOY_ROOT, '.emptygpg'))

    def test_pgp_encrypt_broken_pubkey(self) -> None:
        with self.assertRaises(PGPEncryptionFailed):
            gpg_encrypt_content(b'test', ['dummykey'])

    def test_pgp_vanilla_message(self) -> None:
        user_profile = self.example_user('hamlet')
        user_profile.want_signed_emails = False
        user_profile.want_encrypted_emails = False

        msg = PGPEmailMessage(subject='Subject', body='Email body')
        emails = pgp_sign_and_encrypt(msg, [user_profile])

        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0].subject, 'Subject')
        self.assertEqual(emails[0].body, 'Email body')

        emails[0].send()
        self.assertEqual(len(mail.outbox), 1)

    def test_pgp_sign_message(self) -> None:
        user_profile = self.example_user('hamlet')
        user_profile.want_signed_emails = True
        user_profile.want_encrypted_emails = False

        msg = PGPEmailMessage(subject='Subject', body='Email\nbody')
        emails = pgp_sign_and_encrypt(msg, [user_profile])
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]._signed, True)
        self.check_signed_msg_structure(emails[0].message())

        emails[0].send()
        self.assertEqual(len(mail.outbox), 1)

    def test_pgp_encrypt_message(self) -> None:
        user_profile = self.example_user('hamlet')
        user_profile.want_signed_emails = False
        user_profile.want_encrypted_emails = True

        msg = PGPEmailMessage(subject='Subject', body='Email\nbody')
        emails = pgp_sign_and_encrypt(msg, [user_profile])
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]._encrypted, True)
        self.check_encrypted_msg_structure(emails[0].message(), body='Email\nbody')

        emails[0].send()
        self.assertEqual(len(mail.outbox), 1)

    def test_pgp_force_single_message(self) -> None:
        user_profiles = [self.example_user('hamlet'), self.example_user('othello')]

        user_profiles[0].want_signed_emails = True
        user_profiles[0].want_encrypted_emails = True

        user_profiles[1].want_signed_emails = True
        user_profiles[1].want_encrypted_emails = True

        msg = PGPEmailMessage(subject='Subject', body='Email body')
        emails = pgp_sign_and_encrypt(msg, user_profiles, force_single_message=True)

        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]._signed, True)
        self.assertEqual(emails[0]._encrypted, True)
        self.check_encrypted_msg_structure(emails[0].message(), body='Email body',
                                           signed=True)

        user_profiles[0].want_signed_emails = False
        user_profiles[0].want_encrypted_emails = False

        user_profiles[1].want_signed_emails = False
        user_profiles[1].want_encrypted_emails = False

        emails = pgp_sign_and_encrypt(msg, user_profiles, force_single_message=True)

        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]._signed, False)
        self.assertEqual(emails[0]._encrypted, False)

        user_profiles[0].want_signed_emails = True
        user_profiles[0].want_encrypted_emails = False

        user_profiles[1].want_signed_emails = True
        user_profiles[1].want_encrypted_emails = False

        emails = pgp_sign_and_encrypt(msg, user_profiles, force_single_message=True)

        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]._signed, True)
        self.assertEqual(emails[0]._encrypted, False)
        self.check_signed_msg_structure(emails[0].message())

    def test_pgp_missing_public_key(self) -> None:
        user_profile = self.example_user('cordelia')
        user_profile.want_signed_emails = False
        user_profile.want_encrypted_emails = True

        msg = PGPEmailMessage(subject='Subject', body='Email body')
        with self.assertRaises(PGPKeyNotFound):
            pgp_sign_and_encrypt(msg, [user_profile])

class TestPGPEmailMessageClass(ZulipTestCase):
    def test_pgpemailmessage_setattr_lock(self) -> None:
        pgp_msg = PGPEmailMessage(body='text')
        pgp_msg._locked = True

        with self.assertRaises(AttributeError):
            pgp_msg.body = "text 2"

        # make sure connection can be set even when _locked
        conn = get_connection()
        pgp_msg.connection = conn
        self.assertEqual(pgp_msg.connection, conn)

        pgp_msg._locked = False
        pgp_msg.body = "text 3"
        self.assertEqual(pgp_msg.body, 'text 3')

    def test_pgpemailmessage_set_headers(self) -> None:
        headers = {}  # type: Dict[str, str]
        headers['Custom-Header'] = 'some content'
        headers['From'] = self.example_email('iago')
        msg = PGPEmailMessage(subject='Subject', body='Email body',
                              to = [self.example_email('hamlet')],
                              headers=headers,
                              cc=[self.example_email('cordelia')],
                              reply_to=[settings.DEFAULT_FROM_EMAIL])
        msg._cache_original_message()
        msg._generated_message = SafeMIMEText("text")

        msg._set_headers()
        headers_list = ['Custom-Header', 'Subject', 'To', 'Cc', 'Reply-To']
        assert msg._original_message is not None
        assert msg._generated_message is not None
        for header in headers_list:
            self.assertEqual(msg._generated_message[header],
                             msg._original_message[header])

        self.assertEqual(msg._generated_message['From'], self.example_email('iago'))
