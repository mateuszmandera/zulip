You are using the **Apple auth backend**, but it is not
properly configured. Please check the following:

* You have added `{{ root_domain_uri }}/complete/apple/`
  as the callback URL in Services ID in Apple. You can
  enable "Sign In with Apple" for an app at
  [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/).

* You have set `{{ client_id_key_name }}`, `SOCIAL_AUTH_APPLE_CLIENT`
  in `{{ settings_path }}` and stored the private key file you downloaded
  in `/etc/zulip/apple/zulip-private-key.key` in zulip server
  with proper permissions set.

* Navigate back to the login page and attempt the "Sign in with Apple"
  flow again.
