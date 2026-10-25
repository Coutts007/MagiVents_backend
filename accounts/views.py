import logging

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.forms import PasswordResetForm
from google.auth.exceptions import TransportError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User
from .throttles import LOGIN_THROTTLES, RegisterIPThrottle
from .serializers import (
    GoogleAuthSerializer,
    LoginSerializer,
    PasswordChangeSerializer,
    PasswordResetSerializer,
    RegisterSerializer,
    UserProfileSerializer,
)

logger = logging.getLogger(__name__)

GOOGLE_UNAVAILABLE = 'Google sign-in is not available right now. Please use your email and password.'
GOOGLE_UNREACHABLE = ('We could not reach Google just now. Please try again in a moment, or use your email '
                      'and password.')
GOOGLE_NOT_CONFIRMED = 'Google sign-in could not be confirmed. Please try again.'


def get_tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }


def auth_response(request, user, status_code=status.HTTP_200_OK):
    return Response({
        'tokens': get_tokens_for_user(user),
        'user': UserProfileSerializer(user, context={'request': request}).data,
    }, status=status_code)


class RegisterView(APIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RegisterIPThrottle]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return auth_response(request, user, status.HTTP_201_CREATED)


class LoginView(APIView):
    serializer_class = LoginSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = LOGIN_THROTTLES

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            email=serializer.validated_data['email'].strip().lower(),
            password=serializer.validated_data['password'],
        )
        if user is None:
            # Never says which of the two was wrong, or whether the email has an account
            return Response({'error': 'Incorrect email or password. If you signed up with Google, use '
                                      '"Sign in with Google" instead.'}, status=status.HTTP_401_UNAUTHORIZED)
        return auth_response(request, user)


class GoogleAuthView(APIView):
    serializer_class = GoogleAuthSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if not settings.GOOGLE_OAUTH_CLIENT_ID:
            # The missing setting is for the server log, not for users
            logger.error('Google sign-in was attempted but GOOGLE_OAUTH_CLIENT_ID is not set')
            return Response({'error': GOOGLE_UNAVAILABLE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        try:
            id_info = id_token.verify_oauth2_token(
                serializer.validated_data['credential'],
                google_requests.Request(),
                settings.GOOGLE_OAUTH_CLIENT_ID,
            )
        except TransportError:
            logger.warning('Could not reach Google to check a sign-in', exc_info=True)
            return Response({'error': GOOGLE_UNREACHABLE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except ValueError:
            # Expired, tampered with or issued for another app: which one is not the user's concern
            return Response({'error': GOOGLE_NOT_CONFIRMED}, status=status.HTTP_400_BAD_REQUEST)

        email = (id_info.get('email') or '').lower()
        if not email or not id_info.get('email_verified'):
            return Response({'error': 'Your Google account\'s email address is not verified. Verify it with Google, '
                                      'or sign up with your email and a password.'}, status=status.HTTP_400_BAD_REQUEST)

        name = (id_info.get('name') or email.split('@')[0])[:150]
        avatar_url = id_info.get('picture') or None
        google_sub = id_info.get('sub')

        user = User.objects.filter(email__iexact=email).first()
        if not user and serializer.validated_data['intent'] == 'signin':
            # Safe to say: Google has just proved this person owns the email, so nothing is revealed to a stranger
            return Response({
                'error': f'There is no MagiVents account for {email} yet. Sign up to create one.',
                'code': 'account_not_found',
            }, status=status.HTTP_404_NOT_FOUND)
        if not user:
            user = User(
                username=email,
                email=email,
                first_name=name,
                avatar_url=avatar_url,
                auth_provider='google',
                google_id=google_sub,
                role='patron',
            )
            user.set_unusable_password()
            user.save()
        else:
            update_fields = []
            if not user.google_id:
                user.google_id = google_sub
                update_fields.append('google_id')
            if not user.avatar and not user.avatar_url and avatar_url:
                user.avatar_url = avatar_url
                update_fields.append('avatar_url')
            if update_fields:
                user.save(update_fields=update_fields)

        return auth_response(request, user)


class ProfileView(APIView):
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(UserProfileSerializer(request.user, context={'request': request}).data)

    def patch(self, request):
        serializer = UserProfileSerializer(request.user, data=request.data, partial=True, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


@extend_schema(request={'multipart/form-data': {'type': 'object', 'properties': {'file': {'type': 'string', 'format': 'binary'}}}},
               responses=OpenApiTypes.OBJECT)
class AvatarUploadView(APIView):
    """Handles direct multipart image file uploads for patron profile photos."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'No image file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

        request.user.avatar = file_obj
        request.user.save(update_fields=['avatar'])

        return Response({
            'avatarUrl': request.build_absolute_uri(request.user.avatar.url),
            'message': 'Profile portrait updated successfully.'
        }, status=status.HTTP_200_OK)


class PasswordChangeView(APIView):
    serializer_class = PasswordChangeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data['newPassword'])
        request.user.save(update_fields=['password'])
        return Response({'message': 'Password updated successfully.'})


class PasswordResetView(APIView):
    """Emails a reset link (Django's built-in reset-confirm page). Always returns 200 to avoid leaking accounts."""
    serializer_class = PasswordResetSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        form = PasswordResetForm(data={'email': serializer.validated_data['email']})
        if form.is_valid():
            form.save(request=request, use_https=request.is_secure())
        return Response({'message': 'If an account exists for that email, a reset link has been sent.'})


@extend_schema(request=None)
class BecomeOrganizerView(APIView):
    """Self-service upgrade from attendee to organizer, so the user can create events."""
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        # Only attendees are upgraded; an admin is never turned into a plain organizer
        if request.user.role == 'patron':
            request.user.role = 'curator'
            request.user.save(update_fields=['role'])
        return Response(UserProfileSerializer(request.user, context={'request': request}).data)
