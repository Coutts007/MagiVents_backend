import hashlib

from rest_framework.throttling import SimpleRateThrottle


class LoginEmailThrottle(SimpleRateThrottle):
    """Sign-in attempts per account, wherever they come from."""
    scope = 'login_email'

    def get_cache_key(self, request, view):
        email = str(request.data.get('email') or request.data.get('username') or '').strip().lower()
        if not email:
            return None
        return self.cache_format % {'scope': self.scope, 'ident': hashlib.sha256(email.encode()).hexdigest()}


class IPThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class LoginIPThrottle(IPThrottle):
    scope = 'login_ip'


class RegisterIPThrottle(IPThrottle):
    scope = 'register_ip'


LOGIN_THROTTLES = [LoginIPThrottle, LoginEmailThrottle]
