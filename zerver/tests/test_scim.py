from typing import Any, Dict, Mapping

import orjson
from django.conf import settings
from django.http import HttpResponse

from zerver.lib.actions import do_create_user
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserProfile, get_realm


class TestSCIM(ZulipTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.realm = get_realm("zulip")
        self.scim_bot = do_create_user(
            "scim-bot@zulip.com",
            None,
            self.realm,
            "SCIM Bot",
            bot_type=UserProfile.DEFAULT_BOT,
            acting_user=None,
        )

    def scim_headers(self) -> Mapping[str, str]:
        return {"HTTP_AUTHORIZATION": f"Bearer {settings.SCIM_BEARER_TOKENS['zulip'][0]}"}

    def generate_user_schema(self, user_profile: UserProfile) -> Dict[str, Any]:
        return {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "id": user_profile.id,
            "userName": user_profile.delivery_email,
            "name": {"formatted": user_profile.full_name},
            "displayName": user_profile.full_name,
            "active": True,
            "meta": {
                "resourceType": "User",
                "created": user_profile.date_joined.isoformat(),
                "lastModified": user_profile.date_joined.isoformat(),
                "location": f"http://zulip.testserver/scim/v2/Users/{user_profile.id}",
            },
        }

    def assert_uniqueness_error(self, result: HttpResponse) -> None:
        self.assertEqual(result.status_code, 409)
        output_data = orjson.loads(result.content)

        expected_response_schema = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "detail": "Email address already in use",
            "status": 409,
            "scimType": "uniqueness",
        }
        self.assertEqual(output_data, expected_response_schema)

    def test_get_by_id(self) -> None:
        hamlet = self.example_user("hamlet")
        expected_response_schema = self.generate_user_schema(hamlet)

        result = self.client_get(f"/scim/v2/Users/{hamlet.id}", **self.scim_headers())

        self.assertEqual(result.status_code, 200)
        output_data = orjson.loads(result.content)
        self.assertEqual(output_data, expected_response_schema)

    def test_get_basic_filter_by_username(self) -> None:
        hamlet = self.example_user("hamlet")

        expected_response_schema = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": 1,
            "itemsPerPage": 50,
            "startIndex": 1,
            "Resources": [self.generate_user_schema(hamlet)],
        }

        result = self.client_get(
            f'/scim/v2/Users?filter=userName eq "{hamlet.delivery_email}"', **self.scim_headers()
        )
        self.assertEqual(result.status_code, 200)
        output_data = orjson.loads(result.content)
        self.assertEqual(output_data, expected_response_schema)

        # Now we verify the filter feature doesn't allow access to users
        # on different subdomains.
        different_realm_user = self.mit_user("starnine")
        self.assertNotEqual(different_realm_user.realm_id, hamlet.id)

        result = self.client_get(
            f'/scim/v2/Users?filter=userName eq "{different_realm_user.delivery_email}"',
            **self.scim_headers(),
        )
        self.assertEqual(result.status_code, 200)
        output_data = orjson.loads(result.content)

        expected_empty_results_response_schema = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": 0,
            "itemsPerPage": 50,
            "startIndex": 1,
            "Resources": [],
        }

        self.assertEqual(output_data, expected_empty_results_response_schema)

    def test_get_all_with_pagination(self) -> None:
        realm = get_realm("zulip")

        result_all = self.client_get("/scim/v2/Users", **self.scim_headers())
        self.assertEqual(result_all.status_code, 200)
        output_data_all = orjson.loads(result_all.content)

        expected_response_schema = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": UserProfile.objects.filter(realm=realm, is_bot=False).count(),
            "itemsPerPage": 50,
            "startIndex": 1,
            "Resources": [],
        }
        for user_profile in UserProfile.objects.filter(realm=realm, is_bot=False).order_by("id"):
            user_schema = self.generate_user_schema(user_profile)
            expected_response_schema["Resources"].append(user_schema)

        self.assertEqual(output_data_all, expected_response_schema)

        # Test pagination works, as defined in https://datatracker.ietf.org/doc/html/rfc7644#section-3.4.2.4
        result_offset_limited = self.client_get(
            "/scim/v2/Users?startIndex=4&count=3", **self.scim_headers()
        )
        self.assertEqual(result_offset_limited.status_code, 200)
        output_data_offset_limited = orjson.loads(result_offset_limited.content)
        self.assertEqual(output_data_offset_limited["itemsPerPage"], 3)
        self.assertEqual(output_data_offset_limited["startIndex"], 4)
        self.assertEqual(
            output_data_offset_limited["totalResults"], output_data_all["totalResults"]
        )
        self.assert_length(output_data_offset_limited["Resources"], 3)

        self.assertEqual(output_data_offset_limited["Resources"], output_data_all["Resources"][3:6])

    def test_post(self) -> None:
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "newuser@zulip.com",
            "name": {"formatted": "New User", "familyName": "New", "givenName": "User"},
            "active": True,
        }

        original_user_count = UserProfile.objects.count()
        result = self.client_post(
            "/scim/v2/Users", payload, content_type="application/json", **self.scim_headers()
        )

        self.assertEqual(result.status_code, 201)
        output_data = orjson.loads(result.content)

        new_user_count = UserProfile.objects.count()
        self.assertEqual(new_user_count, original_user_count + 1)

        new_user = UserProfile.objects.last()
        self.assertEqual(new_user.delivery_email, "newuser@zulip.com")
        self.assertEqual(new_user.full_name, "New User")

        expected_response_schema = self.generate_user_schema(new_user)
        self.assertEqual(output_data, expected_response_schema)

    def test_post_email_exists(self) -> None:
        hamlet = self.example_user("hamlet")
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": hamlet.delivery_email,
            "name": {"formatted": "New User", "familyName": "New", "givenName": "User"},
            "active": True,
        }

        result = self.client_post(
            "/scim/v2/Users", payload, content_type="application/json", **self.scim_headers()
        )
        self.assert_uniqueness_error(result)

    def test_delete(self) -> None:
        hamlet = self.example_user("hamlet")
        result = self.client_delete(f"/scim/v2/Users/{hamlet.id}", **self.scim_headers())

        expected_response_schema = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "detail": "Zulip doesn't support DELETE operations on Users. Use PUT or PATCH to modify the active attribute instead.",
            "status": 400,
        }

        self.assertEqual(result.status_code, 400)
        output_data = orjson.loads(result.content)
        self.assertEqual(output_data, expected_response_schema)

    def test_put_change_email_and_name(self) -> None:
        hamlet = self.example_user("hamlet")
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "id": hamlet.id,
            "userName": "bjensen@zulip.com",
            "name": {
                "formatted": "Ms. Barbara J Jensen III",
                "familyName": "Jensen",
                "givenName": "Barbara",
                "middleName": "Jane",
            },
        }
        result = self.json_put(f"/scim/v2/Users/{hamlet.id}", payload, **self.scim_headers())
        self.assertEqual(result.status_code, 200)

        hamlet.refresh_from_db()
        self.assertEqual(hamlet.delivery_email, "bjensen@zulip.com")
        self.assertEqual(hamlet.full_name, "Ms. Barbara J Jensen III")

        output_data = orjson.loads(result.content)
        expected_response_schema = self.generate_user_schema(hamlet)
        self.assertEqual(output_data, expected_response_schema)

    def test_put_change_name_only(self) -> None:
        hamlet = self.example_user("hamlet")
        hamlet_email = hamlet.delivery_email
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "id": hamlet.id,
            "userName": hamlet_email,
            "name": {
                "formatted": "Ms. Barbara J Jensen III",
                "familyName": "Jensen",
                "givenName": "Barbara",
                "middleName": "Jane",
            },
        }
        result = self.json_put(f"/scim/v2/Users/{hamlet.id}", payload, **self.scim_headers())
        self.assertEqual(result.status_code, 200)

        hamlet.refresh_from_db()
        self.assertEqual(hamlet.delivery_email, hamlet_email)
        self.assertEqual(hamlet.full_name, "Ms. Barbara J Jensen III")

        output_data = orjson.loads(result.content)
        expected_response_schema = self.generate_user_schema(hamlet)
        self.assertEqual(output_data, expected_response_schema)

    def test_put_email_exists(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "id": hamlet.id,
            "userName": cordelia.delivery_email,
            "name": {
                "formatted": "Ms. Barbara J Jensen III",
                "familyName": "Jensen",
                "givenName": "Barbara",
                "middleName": "Jane",
            },
        }
        result = self.json_put(f"/scim/v2/Users/{hamlet.id}", payload, **self.scim_headers())
        self.assert_uniqueness_error(result)

    def test_put_deactivate_reactivate_user(self) -> None:
        hamlet = self.example_user("hamlet")
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "id": hamlet.id,
            "userName": hamlet.delivery_email,
            "active": False,
        }
        result = self.json_put(f"/scim/v2/Users/{hamlet.id}", payload, **self.scim_headers())
        self.assertEqual(result.status_code, 200)

        hamlet.refresh_from_db()
        self.assertEqual(hamlet.is_active, False)

        payload["active"] = True
        result = self.json_put(f"/scim/v2/Users/{hamlet.id}", payload, **self.scim_headers())
        self.assertEqual(result.status_code, 200)

        hamlet.refresh_from_db()
        self.assertEqual(hamlet.is_active, True)

    def test_patch_with_path(self) -> None:
        hamlet = self.example_user("hamlet")
        payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "userName", "value": "hamlet_new@zulip.com"}],
        }

        result = self.json_patch(f"/scim/v2/Users/{hamlet.id}", payload, **self.scim_headers())
        self.assertEqual(result.status_code, 200)

        hamlet.refresh_from_db()
        self.assertEqual(hamlet.delivery_email, "hamlet_new@zulip.com")

        output_data = orjson.loads(result.content)
        expected_response_schema = self.generate_user_schema(hamlet)
        self.assertEqual(output_data, expected_response_schema)

        # Multiple operations:
        payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {"op": "replace", "path": "userName", "value": "hamlet_new2@zulip.com"},
                {"op": "replace", "path": "name.formatted", "value": "New Name"},
            ],
        }
        result = self.json_patch(f"/scim/v2/Users/{hamlet.id}", payload, **self.scim_headers())
        self.assertEqual(result.status_code, 200)

        hamlet.refresh_from_db()
        self.assertEqual(hamlet.full_name, "New Name")
        self.assertEqual(hamlet.delivery_email, "hamlet_new2@zulip.com")

        output_data = orjson.loads(result.content)
        expected_response_schema = self.generate_user_schema(hamlet)
        self.assertEqual(output_data, expected_response_schema)

    def test_patch_without_path(self) -> None:
        hamlet = self.example_user("hamlet")
        payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "value": {"userName": "hamlet_new@zulip.com"}}],
        }

        result = self.json_patch(f"/scim/v2/Users/{hamlet.id}", payload, **self.scim_headers())
        self.assertEqual(result.status_code, 200)

        hamlet.refresh_from_db()
        self.assertEqual(hamlet.delivery_email, "hamlet_new@zulip.com")

        output_data = orjson.loads(result.content)
        expected_response_schema = self.generate_user_schema(hamlet)
        self.assertEqual(output_data, expected_response_schema)
