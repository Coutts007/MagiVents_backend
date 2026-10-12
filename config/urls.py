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
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.views import (
    AvatarUploadView,
    BecomeOrganizerView,
    GoogleAuthView,
    LoginView,
    PasswordChangeView,
    PasswordResetView,
    ProfileView,
    RegisterView,
)
from core.views import GeocodeView, HealthView, PublicConfigView
from payments.views import BookingPayView, BookingStatusView
from gatherings.views import GatheringViewSet
from tickets.views import (
    BookingViewSet,
    BookmarkListView,
    BookmarkToggleView,
    PromoCodeViewSet,
    PromoValidateView,
    TicketDetailView,
    TicketLookupView,
    TicketPdfView,
    TicketResendView,
)

router = DefaultRouter()
router.register(r'gatherings', GatheringViewSet, basename='gatherings')
router.register(r'bookings', BookingViewSet, basename='bookings')
router.register(r'promo-codes', PromoCodeViewSet, basename='promo-codes')

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
    path('api/auth/become-organizer/', BecomeOrganizerView.as_view(), name='become_organizer'),
    # Django's built-in pages that the password reset email links to
    path('accounts/', include('django.contrib.auth.urls')),

    # Bookmarks
    path('api/bookmarks/', BookmarkListView.as_view(), name='bookmark_list'),
    path('api/bookmarks/toggle/<str:gathering_id>/', BookmarkToggleView.as_view(), name='bookmark_toggle'),

    # Public configuration and API documentation
    path('api/health/', HealthView.as_view(), name='health'),
    path('api/config/', PublicConfigView.as_view(), name='public_config'),
    path('api/geocode/', GeocodeView.as_view(), name='geocode'),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger_ui'),

    # Promo code check at checkout (must come before the router's promo-codes/<pk>/ route)
    path('api/promo-codes/validate/', PromoValidateView.as_view(), name='promo_validate'),

    # Paid booking lifecycle: guests poll by booking UUID, and retry without creating a new booking
    path('api/bookings/<uuid:booking_id>/status/', BookingStatusView.as_view(), name='booking_status'),
    path('api/bookings/<uuid:booking_id>/pay/', BookingPayView.as_view(), name='booking_pay'),
    path('api/payments/', include('payments.urls')),

    # Tickets by booking UUID (QR code / email link), and guest lookup by code + email
    path('api/tickets/lookup/', TicketLookupView.as_view(), name='ticket_lookup'),
    path('api/tickets/<uuid:booking_id>/', TicketDetailView.as_view(), name='ticket_detail'),
    path('api/tickets/<uuid:booking_id>/pdf/', TicketPdfView.as_view(), name='ticket_pdf'),
    path('api/tickets/<uuid:booking_id>/resend/', TicketResendView.as_view(), name='ticket_resend'),

    # Gathering & Booking Resources
    # Staff console: secret path + staff account; anything else is a plain 404
    path('api/console/', include('console.urls')),

    path('api/', include(router.urls)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
