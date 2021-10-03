from typing import Any, Callable, Dict, List, Optional, Type, Union

import django_scim.constants as scim_constants
import django_scim.exceptions as scim_exceptions
from django.conf import settings
from django.core.validators import ValidationError, validate_email
from django.db import models, transaction
from django.http import HttpRequest
from django_scim.adapters import SCIMUser
from scim2_filter_parser.attr_paths import AttrPath

from zerver.lib.actions import (
    do_change_user_delivery_email,
    do_create_user,
    do_deactivate_user,
    do_reactivate_user,
)
from zerver.lib.request import RequestNotes
from zerver.lib.subdomains import get_subdomain
from zerver.models import UserProfile, get_user_by_delivery_email


class ZulipSCIMUser(SCIMUser):
    id_field = "id"

    def __init__(self, obj: UserProfile, request: Optional[HttpRequest] = None) -> None:
        # We keep the function signature from the superclass, but this actually
        # shouldn't be called with request being None.
        assert request is not None

        # self.obj is populated appropriately by django-scim2 views with
        # an instance of UserProfile - either fetched from the database
        # or constructed via UserProfile() if the request currently being
        # handled is a User creation request (POST).
        self.obj: UserProfile

        super().__init__(obj, request)
        self.subdomain = get_subdomain(request)
        self.config = settings.SCIM_CONFIG[self.subdomain]

        self._email_new_value: Optional[str] = None
        self._is_active_new_value: Optional[bool] = None
        self._full_name_new_value: Optional[str] = None
        self._password_set_to: Optional[str] = None

    def is_new_user(self) -> bool:
        return not bool(self.obj.id)

    @property
    def display_name(self) -> str:
        """
        Return the displayName of the user per the SCIM spec.

        Overriden because UserProfile uses the .full_name attribute,
        while the superclass expects .first_name and .last_name.
        """
        return self.obj.full_name

    def to_dict(self) -> Dict[str, Any]:
        """
        Return a ``dict`` conforming to the SCIM User Schema,
        ready for conversion to a JSON object.
        """
        if self.config["name_formatted_included"]:
            name = {
                "formatted": self.obj.full_name,
            }
        else:
            if " " not in self.obj.full_name:
                first_name, last_name = "", self.obj.full_name
            else:
                first_name, last_name = self.obj.full_name.split(" ", 1)
            name = {
                "givenName": first_name,
                "familyName": last_name,
            }
        d = dict(
            {
                "schemas": [scim_constants.SchemaURI.USER],
                "id": self.obj.id,
                "userName": self.obj.delivery_email,
                "name": name,
                "displayName": self.display_name,
                "active": self.obj.is_active,
                # meta is a property implemented in the superclass
                "meta": self.meta,
            }
        )

        return d

    def from_dict(self, d: Dict[str, Any]) -> None:
        """
        Consume a ``dict`` conforming to the SCIM User Schema. The dict is originally submitted
        as JSON by the client in PUT (update a user) and POST (create a new user) requests.
        A PUT request tells us to update User attributes to match those passed in the dict.
        A POST request tells us to create a new User with attributes as specified in the dict.

        Completely overriden because we handle things differently than the superclass. We just
        store the values of the supported attributes before actually proceeding with making changes
        in self.save().
        """
        email = d.get("userName")
        assert isinstance(email, str)
        self.validate_email(email)
        if self.obj.delivery_email != email:
            self._email_new_value = email

        name_attr_dict = d.get("name", {})
        if self.config["name_formatted_included"]:
            full_name = name_attr_dict.get("formatted", "")
        else:
            # Some providers (e.g. Okta) don't provide name.formatted.
            first_name = name_attr_dict.get("givenName", "")
            last_name = name_attr_dict.get("familyName", "")
            full_name = f"{first_name} {last_name}".strip()

        if full_name and self.obj.full_name != full_name:
            assert isinstance(full_name, str)
            self._full_name_new_value = full_name

        if self.is_new_user() and not full_name:
            raise scim_exceptions.BadRequestError(
                "Must specify name.formatted when creating a new user"
            )

        cleartext_password = d.get("password")
        if cleartext_password:
            assert isinstance(cleartext_password, str)
            self._password_set_to = cleartext_password

        active = d.get("active")
        if self.is_new_user() and not active:
            raise scim_exceptions.BadRequestError("New user must have active=True")

        elif active is not None and active != self.obj.is_active:
            assert isinstance(active, bool)
            self._is_active_new_value = active

    def handle_replace(
        self,
        path: Optional[AttrPath],
        value: Union[str, List[object], Dict[AttrPath, object]],
        operation: Any,
    ) -> None:
        """
        PATCH requests specify a list of operations of types "add", "remove", "replace".
        So far we only implement "replace" as that should be sufficient.

        This method is forked from the superclass and is called to handle "replace"
        PATCH operations. Such an operation tells us to change the values
        of a User's attributes as specified.
        """
        if not isinstance(value, dict):
            # Restructure for use in loop below. Taken from the overriden method.
            if path is None:
                raise scim_exceptions.BadRequestError("Invalid path/value format")
            value = {path: value}

        assert isinstance(value, dict)
        for path, val in (value or {}).items():
            if path.first_path == ("userName", None, None):
                assert isinstance(val, str)
                self.validate_email(val)
                self._email_new_value = val
            elif path.first_path == ("name", "formatted", None):
                assert isinstance(val, str)
                self._full_name_new_value = val
            elif path.first_path == ("active", None, None):
                assert isinstance(val, bool)
                self._is_active_new_value = val

            else:
                raise scim_exceptions.NotImplementedError("Not Implemented")

        self.save()

    def save(self) -> None:
        realm = RequestNotes.get_notes(self._request).realm
        assert realm is not None

        email_new_value = getattr(self, "_email_new_value", None)
        is_active_new_value = getattr(self, "_is_active_new_value", None)
        full_name_new_value = getattr(self, "_full_name_new_value", None)
        password = getattr(self, "_password_set_to", None)

        # Clean up the internal state now that we've fetched the values:
        self._email_new_value = None
        self._is_active_new_value = None
        self._full_name_new_value = None
        self._password_set_to = None

        if email_new_value:
            # TODO: Add more email validation like in create_user_backend?
            try:
                get_user_by_delivery_email(email_new_value, realm)
            except UserProfile.DoesNotExist:
                pass
            else:
                raise ConflictError("Email address already in use")

        if self.is_new_user():
            self.obj = do_create_user(
                email_new_value,
                password,
                realm,
                full_name_new_value,
                acting_user=None,
            )
            return

        with transaction.atomic():
            if full_name_new_value:
                self.obj.full_name = full_name_new_value
                self.obj.save(update_fields=["full_name"])

            if email_new_value:
                do_change_user_delivery_email(self.obj, email_new_value)

            if is_active_new_value is not None and is_active_new_value:
                do_reactivate_user(self.obj, acting_user=None)
            elif is_active_new_value is not None and not is_active_new_value:
                do_deactivate_user(self.obj, acting_user=None)

    def delete(self) -> None:
        """
        This is consistent with Okta SCIM - users don't get DELETEd, they're deactivated
        by changing their "active" attr to False.
        """
        raise scim_exceptions.BadRequestError(
            "Zulip doesn't support DELETE operations on Users. Use PUT or PATCH to modify the active attribute instead."
        )

    @staticmethod
    def validate_email(email: str) -> None:
        try:
            validate_email(email)
        except ValidationError:
            raise scim_exceptions.BadRequestError("Invalid email value")


def get_extra_model_filter_kwargs_getter(
    model: Type[models.Model],
) -> Callable[[HttpRequest, Any, Any], Dict[str, object]]:
    """
    Returns a function which generates addition kwargs
    to add to QuerySet's .filter() when fetching a UserProfile
    corresponding to the requested SCIM User from the database.
    It's *crucial* to add filtering by realm_id (based on the
    subdomain of the request) to prevent a SCIM client authorized
    for subdomain X from being able to access all of the Users
    on the entire server.

    This should be extended for Groups when implementing them.
    """

    def get_extra_filter_kwargs(
        request: HttpRequest, *args: Any, **kwargs: Any
    ) -> Dict[str, object]:
        realm = RequestNotes.get_notes(request).realm
        assert realm is not None
        return {"realm_id": realm.id, "is_bot": False}

    return get_extra_filter_kwargs


def base_scim_location_getter(request: HttpRequest, *args: Any, **kwargs: Any) -> str:
    """
    Used as the base url for constructing the Location of a SCIM resource.
    We consider each <subdomain>.<root domain> to be an independent, separate
    "bucket", so Location of a resource is dependent on the subdomain.
    """

    realm = RequestNotes.get_notes(request).realm
    assert realm is not None

    return realm.uri


class ConflictError(scim_exceptions.IntegrityError):
    """
    Per https://datatracker.ietf.org/doc/html/rfc7644#section-3.3

    If the service provider determines that the creation of the requested
    resource conflicts with existing resources (e.g., a "User" resource
    with a duplicate "userName"), the service provider MUST return HTTP
    status code 409 (Conflict) with a "scimType" error code of
    "uniqueness"

    scim_exceptions.IntegrityError class omits to include the scimType.
    """

    scim_type = "uniqueness"
