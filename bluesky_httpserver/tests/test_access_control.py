# Tests for user authorization and authentication on the working server
import pytest
import os
import pprint

from bluesky_queueserver.manager.tests.common import (  # noqa F401
    re_manager,
    re_manager_cmd,
)
from .conftest import fastapi_server_fs  # noqa: F401
from .conftest import request_to_json

from bluesky_httpserver.authorization._defaults import (
    _DEFAULT_ROLE_SINGLE_USER,
    _DEFAULT_SCOPES_SINGLE_USER,
    _DEFAULT_ROLE_PUBLIC,
    _DEFAULT_SCOPES_PUBLIC,
    _DEFAULT_ROLES,
)


def _setup_server_with_config_file(*, config_file_str, tmpdir, monkeypatch):
    """
    Creates config file for the server in ``tmpdir/config/`` directory and
    sets up the respective environment variable. Sets ``tmpdir`` as a current directory.
    """
    config_fln = "config_httpserver.yml"
    config_dir = os.path.join(tmpdir, "config")
    config_path = os.path.join(config_dir, config_fln)
    os.makedirs(config_dir)
    with open(config_path, "wt") as f:
        f.writelines(config_file_str)

    monkeypatch.setenv("QSERVER_HTTP_SERVER_CONFIG", config_path)
    monkeypatch.chdir(tmpdir)

    return config_path


config_noauth_with_anonymous_access = """
authentication:
    allow_anonymous_access: True
"""

config_noauth_without_anonymous_access = """
authentication:
    allow_anonymous_access: False
"""

config_noauth_single_user_api_key = """
authentication:
    single_user_api_key: "apikeyfromconfig"
"""

config_toy_with_anonymous_access = """
authentication:
    allow_anonymous_access: True
    providers:
        - provider: toy
          authenticator: bluesky_httpserver.authenticators:DictionaryAuthenticator
          args:
              users_to_passwords:
                  bob: bob_password
                  alice: alice_password
                  cara: cara_password
                  tom: tom_password
"""

config_toy_without_anonymous_access = """
authentication:
    allow_anonymous_access: False
    providers:
        - provider: toy
          authenticator: bluesky_httpserver.authenticators:DictionaryAuthenticator
          args:
              users_to_passwords:
                  bob: bob_password
                  alice: alice_password
                  cara: cara_password
                  tom: tom_password
"""

authorization_dict = """
api_access:
  policy: bluesky_httpserver.authorization:DictionaryAPIAccessControl
  args:
    users:
      bob:
        roles:
          - admin
          - expert
      alice:
        roles:
          - user
      cara:
        roles:
          - observer
"""

config_noauth_modify_default_roles = f"""
authentication:
  allow_anonymous_access: True
api_access:
  policy: bluesky_httpserver.authorization:DictionaryAPIAccessControl
  args:
    roles:
      {_DEFAULT_ROLE_SINGLE_USER}:
        scopes_add:
          - admin:apikeys
          - admin:read:principals
          - admin:metrics
        scopes_remove:
          - read:monitor
      {_DEFAULT_ROLE_PUBLIC}:
        scopes_set:
          - read:status
          - read:queue
          - read:history
"""


# fmt: on
@pytest.mark.parametrize(
    "cfg, access_cfg, single_user_access, public_access, token_access",
    [
        (config_toy_with_anonymous_access, authorization_dict, False, True, True),
        (config_toy_without_anonymous_access, authorization_dict, False, False, True),
        (config_toy_with_anonymous_access, "", False, True, False),
        (config_toy_without_anonymous_access, "", False, False, False),
        (config_noauth_with_anonymous_access, authorization_dict, True, True, False),
        (config_noauth_without_anonymous_access, authorization_dict, True, False, False),
        (config_noauth_with_anonymous_access, "", True, True, False),
        (config_noauth_without_anonymous_access, "", True, False, False),
        ("", authorization_dict, True, False, False),  # No authentication settings in config
        ("", "", True, False, False),  # No config file
    ],
)
# fmt: off
def test_authentication_and_authorization_01(
    tmpdir,
    monkeypatch,
    re_manager,  # noqa: F811
    fastapi_server_fs,  # noqa: F811
    cfg,
    access_cfg,
    single_user_access,  # Access in 'single-user' mode
    public_access,  # Public unauthenticated user works
    token_access,
):
    """
    Basic test: attempt to log into the server configured using various combinations of settings.

    Tested behavior:
        - Public unauthenticated access is disabled by default. Can be enabled by setting
          'allow_anonymous_access' True in the config file. (It can also be set using EV, but
          this option is not tested here.)
        - Single-user access is disabled if any authentication providers are listed in the config file.
        - Token is generated by logging in using valid username and password if the user is known
          to the authorization manager and the list of scopes is not empty. If the list of scopes
          is empty, then no token is generated (the user should not access the server).
        - Login with incorrect username or password does not work (token is not generated).
        - API can not be accessed using incorrect token or API key.
    """
    config = cfg + access_cfg
    providers_set = "providers" in config
    api_access_set = "api_access" in config

    if config:
        _setup_server_with_config_file(config_file_str=config, tmpdir=tmpdir, monkeypatch=monkeypatch)
    fastapi_server_fs()

    # Test if anonymous 'public' access works
    resp1 = request_to_json("get", "/status", api_key=None)
    if public_access:
        assert "msg" in resp1, pprint.pformat(resp1)
        assert "RE Manager" in resp1["msg"]
    else:
        assert "detail" in resp1, pprint.pformat(resp1)
        assert "Not enough permissions" in resp1["detail"]

    # Make sure that the anonymous 'single-user' access is not allowed
    resp2 = request_to_json("get", "/status")  # By default, the single user API key is sent
    if single_user_access:
        assert "msg" in resp2, pprint.pformat(resp1)
        assert "RE Manager" in resp2["msg"]
    else:
        assert "detail" in resp2, pprint.pformat(resp2)
        assert "Invalid API key" in resp2["detail"]

    if api_access_set:
        login_fail_msg = "Incorrect username or password"
    else:
        login_fail_msg = "User is not authorized to access the server"
    login_fail_msg = login_fail_msg if providers_set else "Not Found"

    # auth_fail_msg1 = "Incorrect username or password" if providers_set else "Not Found"
    auth_fail_msg = "Incorrect username or password" if providers_set else "Not Found"

    # Login using token: should work in all cases
    resp3 = request_to_json("post", "/auth/provider/toy/token", login=("bob", "bob_password"))
    if token_access:
        assert "access_token" in resp3
        token = resp3["access_token"]
        resp4 = request_to_json("get", "/status", token=token)
        assert "msg" in resp4, pprint.pformat(resp4)
        assert "RE Manager" in resp4["msg"]
    else:
        assert "detail" in resp3
        assert login_fail_msg in resp3["detail"]

    # Login using incorrect username
    resp5 = request_to_json("post", "/auth/provider/toy/token", login=("incorrect_name", "bob_password"))
    assert "detail" in resp5
    assert auth_fail_msg in resp5["detail"]

    # Login using invalid password
    resp6 = request_to_json("post", "/auth/provider/toy/token", login=("bob", "invalid_password"))
    assert "detail" in resp6
    assert auth_fail_msg in resp6["detail"]

    # Try using invalid token
    resp7 = request_to_json("get", "/status", token="INVALIDTOKEN")
    assert "detail" in resp7, pprint.pformat(resp7)
    assert "Could not validate credentials" in resp7["detail"]


def test_authentication_and_authorization_02(
    tmpdir,
    monkeypatch,
    re_manager,  # noqa: F811
    fastapi_server_fs,  # noqa: F811
):
    """
    Pass single-user API key in config file.
    """

    config = config_noauth_single_user_api_key
    _setup_server_with_config_file(config_file_str=config, tmpdir=tmpdir, monkeypatch=monkeypatch)
    fastapi_server_fs()

    api_key = "apikeyfromconfig"

    resp1 = request_to_json("get", "/status", api_key=api_key)
    assert "msg" in resp1, pprint.pformat(resp1)
    assert "RE Manager" in resp1["msg"]

    roles = [_DEFAULT_ROLE_SINGLE_USER]
    scopes = set(_DEFAULT_SCOPES_SINGLE_USER)

    resp2a = request_to_json("get", "/auth/scopes", api_key=api_key)
    assert "roles" in resp2a, pprint.pformat(resp2a)
    assert "scopes" in resp2a, pprint.pformat(resp2a)
    assert resp2a["roles"] == roles
    assert set(resp2a["scopes"]) == scopes


def test_authentication_and_authorization_03(
    tmpdir,
    monkeypatch,
    re_manager,  # noqa: F811
    fastapi_server_fs,  # noqa: F811
):
    """
    Check default scopes for 'single-user' and public access. No authentication providers
    or authorization policy are defined in the config file.
    """

    config = config_noauth_with_anonymous_access
    _setup_server_with_config_file(config_file_str=config, tmpdir=tmpdir, monkeypatch=monkeypatch)
    fastapi_server_fs()

    # Check that both single-user access and public access work
    #   (by default 'api_key' is set to valid single-user API key)
    for params in ({}, {"api_key": None}):
        print(f"Test case: params={params}")

        resp1 = request_to_json("get", "/status", **params)
        assert "msg" in resp1, pprint.pformat(resp1)
        assert "RE Manager" in resp1["msg"]

        if not params:
            roles = [_DEFAULT_ROLE_SINGLE_USER]
            scopes = set(_DEFAULT_SCOPES_SINGLE_USER)
        else:
            roles = [_DEFAULT_ROLE_PUBLIC]
            scopes = set(_DEFAULT_SCOPES_PUBLIC)

        resp2a = request_to_json("get", "/auth/scopes", **params)
        assert "roles" in resp2a, pprint.pformat(resp2a)
        assert "scopes" in resp2a, pprint.pformat(resp2a)
        assert resp2a["roles"] == roles
        assert set(resp2a["scopes"]) == scopes


def test_authentication_and_authorization_04(
    tmpdir,
    monkeypatch,
    re_manager,  # noqa: F811
    fastapi_server_fs,  # noqa: F811
):
    """
    Check default scopes for 'single-user' and public access. No authentication providers
    or authorization policy are defined in the config file.
    """

    config = config_noauth_modify_default_roles
    _setup_server_with_config_file(config_file_str=config, tmpdir=tmpdir, monkeypatch=monkeypatch)
    fastapi_server_fs()

    # Check that both single-user access and public access work
    #   (by default 'api_key' is set to valid single-user API key)
    for params in ({}, {"api_key": None}):
        print(f"Test case: params={params}")

        resp1 = request_to_json("get", "/status", **params)
        assert "msg" in resp1, pprint.pformat(resp1)
        assert "RE Manager" in resp1["msg"]

        if not params:
            roles = [_DEFAULT_ROLE_SINGLE_USER]
            scopes_to_add = {"admin:apikeys", "admin:read:principals", "admin:metrics"}
            scopes_to_remove = set(["read:monitor"])
            scopes = (set(_DEFAULT_SCOPES_SINGLE_USER) | scopes_to_add) - scopes_to_remove
        else:
            roles = [_DEFAULT_ROLE_PUBLIC]
            scopes = {"read:status", "read:queue", "read:history"}

        resp2a = request_to_json("get", "/auth/scopes", **params)
        assert "roles" in resp2a, pprint.pformat(resp2a)
        assert "scopes" in resp2a, pprint.pformat(resp2a)
        assert resp2a["roles"] == roles
        assert set(resp2a["scopes"]) == set(scopes)


def test_authentication_and_authorization_05(
    tmpdir,
    monkeypatch,
    re_manager,  # noqa: F811
    fastapi_server_fs,  # noqa: F811
):
    """
    Check default scopes for logged in user. Test management of scopes when using authorization
    with token, generating API key using a token, generating API key using another API key.
    Check that the new API key has the same scope as the existing key if the scopes are inherited.
    Verify that the scope can not be extended.
    """

    config = config_toy_without_anonymous_access + authorization_dict
    _setup_server_with_config_file(config_file_str=config, tmpdir=tmpdir, monkeypatch=monkeypatch)
    fastapi_server_fs()

    n_api_keys = 0

    # Check that both single-user access and public access work
    #   (by default 'api_key' is set to valid single-user API key)
    for username in ("bob", "alice", "cara"):
        print(f"Testing access for the username {username!r}")

        resp1 = request_to_json("post", "/auth/provider/toy/token", login=(username, username + "_password"))
        assert "access_token" in resp1
        token = resp1["access_token"]

        resp2 = request_to_json("get", "/status", token=token)
        assert "msg" in resp2, pprint.pformat(resp2)
        assert "RE Manager" in resp2["msg"]

        roles_all = {"bob": ["admin", "expert"], "alice": ["user"], "cara": ["observer"]}
        roles_user = roles_all[username]
        scopes_user = set()
        for role in roles_user:
            scopes_user = scopes_user | set(_DEFAULT_ROLES[role])

        resp3 = request_to_json("get", "/auth/scopes", token=token)
        assert "roles" in resp3, pprint.pformat(resp3)
        assert "scopes" in resp3, pprint.pformat(resp3)
        assert resp3["roles"] == roles_user
        assert set(resp3["scopes"]) == scopes_user

        # Get an API key based on the token. Inherit (by default) all the scopes
        resp4 = request_to_json(
            "post", "/auth/apikey", json={"expires_in": 900, "note": "API key for testing"}, token=token
        )
        if "user:apikeys" in scopes_user:
            assert "secret" in resp4, pprint.pformat(resp4)
            assert "note" in resp4, pprint.pformat(resp4)
            assert resp4["note"] == "API key for testing"
            assert resp4["scopes"] == ["inherit"]
            api_key = resp4["secret"]

            resp4a = request_to_json("get", "/auth/scopes", api_key=api_key)
            assert "roles" in resp4a, pprint.pformat(resp4a)
            assert "scopes" in resp4a, pprint.pformat(resp4a)
            assert resp4a["roles"] == roles_user
            assert set(resp4a["scopes"]) == scopes_user

            resp5 = request_to_json("get", "/status", api_key=api_key)
            assert "msg" in resp5, pprint.pformat(resp5)
            assert "RE Manager" in resp5["msg"]

            # Generate the new API key based on the existing API key based on limited scopes
            new_scopes = ["read:status", "user:apikeys"]
            resp6 = request_to_json(
                "post", "/auth/apikey", json={"scopes": new_scopes, "expires_in": 900}, api_key=api_key
            )
            assert "secret" in resp6, pprint.pformat(resp6)
            assert "note" in resp6, pprint.pformat(resp6)
            assert resp6["note"] is None
            assert resp6["scopes"] == new_scopes
            api_key2 = resp6["secret"]

            resp6a = request_to_json("get", "/auth/scopes", api_key=api_key2)
            assert "roles" in resp6a, pprint.pformat(resp6a)
            assert "scopes" in resp6a, pprint.pformat(resp6a)
            assert resp6a["roles"] == roles_user
            assert set(resp6a["scopes"]) == set(new_scopes)

            resp7 = request_to_json("get", "/status", api_key=api_key2)
            assert "msg" in resp7, pprint.pformat(resp7)
            assert "RE Manager" in resp7["msg"]

            # Generate another API key that inherits the scope from the existing API key
            resp8 = request_to_json("post", "/auth/apikey", json={"expires_in": 900}, api_key=api_key2)
            assert "secret" in resp8, pprint.pformat(resp8)
            assert "note" in resp8, pprint.pformat(resp8)
            assert resp8["note"] is None
            assert set(resp8["scopes"]) == set(new_scopes)
            api_key3 = resp8["secret"]

            resp8a = request_to_json("get", "/auth/scopes", api_key=api_key3)
            assert "roles" in resp8a, pprint.pformat(resp8a)
            assert "scopes" in resp8a, pprint.pformat(resp8a)
            assert resp8a["roles"] == roles_user
            assert set(resp8a["scopes"]) == set(new_scopes)

            resp9 = request_to_json("get", "/status", api_key=api_key3)
            assert "msg" in resp9, pprint.pformat(resp9)
            assert "RE Manager" in resp9["msg"]

            # Try to expande the scope while generating an API key
            resp10 = request_to_json(
                "post", "/auth/apikey", json={"scopes": ["admin:apikeys"], "expires_in": 900}, api_key=api_key3
            )
            assert "detail" in resp10, pprint.pformat(resp10)
            assert "must be a subset of the allowed principal's scopes" in resp10["detail"]

            n_api_keys += 1

        else:
            assert "detail" in resp4
            assert "Not enough permissions" in resp4["detail"]

        if not n_api_keys:
            assert False, "No API keys were generated during the test. The test may be incorrectly configured."

        resp11 = request_to_json("post", "/auth/provider/toy/token", login=("tom", "tom_password"))
        assert "detail" in resp11
        assert "User is not authorized to access the server" in resp11["detail"]

        resp12 = request_to_json("post", "/auth/provider/toy/token", login=("random", "random_password"))
        assert "detail" in resp12
        assert "Incorrect username or password" in resp12["detail"]
