# backend/events/urls.py
from django.urls import path
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

urlpatterns = [
    # JWT Authentication Endpoints
    path('auth/login/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]

# backend/events/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import EventViewSet, UserRegistrationView, TicketTierViewSet

# Create a router and register our ViewSet
router = DefaultRouter()
router.register(r'events', EventViewSet, basename='event')
router.register(r'ticket-tiers', TicketTierViewSet, basename='tickettier') # <-- Added Ticket Tiers

urlpatterns = [
    # JWT Authentication Endpoints

    path('auth/register/', UserRegistrationView.as_view(), name='user_register'), # <-- Added Registration
    path('auth/login/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # Event CRUD Endpoints (maps to /api/events/)
    path('', include(router.urls)),
]