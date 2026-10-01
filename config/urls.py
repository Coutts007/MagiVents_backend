"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.views import (
    AvatarUploadView,
    GoogleAuthView,
    LoginView,
    PasswordChangeView,
    PasswordResetView,
    ProfileView,
    RegisterView,
)
from gatherings.views import GatheringViewSet
from tickets.views import BookingViewSet, BookmarkListView, BookmarkToggleView

router = DefaultRouter()
router.register(r'gatherings', GatheringViewSet, basename='gatherings')
router.register(r'bookings', BookingViewSet, basename='bookings')

urlpatterns = [
    path('admin/', admin.site.urls),

    # Auth Endpoints
    path('api/auth/register/', RegisterView.as_view(), name='register'),
    path('api/auth/login/', LoginView.as_view(), name='login'),
    path('api/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/auth/google/', GoogleAuthView.as_view(), name='google_auth'),
    path('api/auth/profile/', ProfileView.as_view(), name='user_profile'),
    path('api/auth/avatar/upload/', AvatarUploadView.as_view(), name='avatar_upload'),
    path('api/auth/password/change/', PasswordChangeView.as_view(), name='password_change_api'),
    path('api/auth/password/reset/', PasswordResetView.as_view(), name='password_reset_api'),
    # Django's built-in pages that the password reset email links to
    path('accounts/', include('django.contrib.auth.urls')),

    # Bookmarks
    path('api/bookmarks/', BookmarkListView.as_view(), name='bookmark_list'),
    path('api/bookmarks/toggle/<str:gathering_id>/', BookmarkToggleView.as_view(), name='bookmark_toggle'),

    # Gathering & Booking Resources
    path('api/', include(router.urls)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
