from rest_framework.permissions import BasePermission


class IsPlatformAdmin(BasePermission):
    """Admin-only endpoints: every signed-in user who is not an active admin gets 403."""
    message = 'Only MagiVents administrators can do this.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_platform_admin)
