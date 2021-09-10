from typing import List, Optional, Tuple

from django.http import HttpRequest
from django_scim.filters import UserFilterQuery

from zerver.lib.subdomains import get_subdomain


# This is in a separate file due to circular import issues django-scim2 runs into
# when this is placed in zerver.lib.scim.
class ZulipUserFilterQuery(UserFilterQuery):
    """
    This class implements the filter functionality of SCIM2.
    E.g. requests such as
    /scim/v2/Users?filter=userName eq "hamlet@zulip.com"
    can be made to refer to resources via their properties.
    This gets fairly complicated in its full scope
    (https://datatracker.ietf.org/doc/html/rfc7644#section-3.4.2.2)
    and django-scim2 implements an entire mechanism of converting
    this SCIM2 filter syntax into SQL queries.

    What we have to do in this class is to customize a few parts so that
    django-scim2 can know how to make correct translation into SQL.
    """

    # The attr_map describes to which table.column the given SCIM2 User
    # attributes refer to.
    attr_map = {
        # attr, sub attr, uri
        ("userName", None, None): "zerver_userprofile.delivery_email",
        ("name", "formatted", None): "zerver_userprofile.full_name",
        ("active", None, None): "zerver_userprofile.is_active",
    }

    # joins tells django-scim2 to always add the specified JOINS
    # to the formed SQL queries. We need to JOIN the Realm table
    # because we need to limit the results to the realm (subdomain)
    # of the request.
    joins = ("INNER JOIN zerver_realm ON zerver_realm.id = realm_id",)

    @classmethod
    def get_extras(cls, q: str, request: Optional[HttpRequest] = None) -> Tuple[str, List[str]]:
        """
        Return extra SQL and params to be attached to end of current Query's
        SQL and params.

        Here we ensure that results are limited to the subdomain of the request
        and also exclude bots, as we currently don't want them to be managed by SCIM2.
        """
        assert request is not None
        subdomain = get_subdomain(request)

        return "AND zerver_realm.string_id = %s AND zerver_userprofile.is_bot = False", [subdomain]
