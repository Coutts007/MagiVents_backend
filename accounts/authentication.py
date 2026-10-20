"""
JWT sign-in with the user record cached for AUTH_USER_CACHE_SECONDS.

Every signed-in request used to load the user from the database just to check the token's owner; under load that
was the largest single cost of a request. The cached copy is dropped whenever the user is saved or deleted
(accounts.signals), so suspensions, role changes and password changes apply to the next request.
"""
from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.utils import get_md5_hash_password


def user_cache_key(user_id):
    return f'auth-user:{user_id}'


class CachedJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user_id = validated_token.get(api_settings.USER_ID_CLAIM)
        if user_id is None or settings.AUTH_USER_CACHE_SECONDS <= 0:
            return super().get_user(validated_token)

        key = user_cache_key(user_id)
        user = cache.get(key)
        if user is None:
            user = super().get_user(validated_token)
            cache.set(key, user, settings.AUTH_USER_CACHE_SECONDS)
            return user

        # The checks JWTAuthentication.get_user makes, on the cached copy
        if api_settings.CHECK_USER_IS_ACTIVE and not user.is_active:
            raise AuthenticationFailed(_('User is inactive'), code='user_inactive')
        if api_settings.CHECK_REVOKE_TOKEN and \
                validated_token.get(api_settings.REVOKE_TOKEN_CLAIM) != get_md5_hash_password(user.password):
            raise AuthenticationFailed(_("The user's password has been changed."), code='password_changed')
        return user


from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme  # noqa: E402


class CachedJWTScheme(SimpleJWTScheme):
    """API docs: the same bearer-token scheme as plain JWTAuthentication."""
    target_class = 'accounts.authentication.CachedJWTAuthentication'
