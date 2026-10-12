from django.urls import path

from . import views

# Every route starts with the secret from ADMIN_CONSOLE_PATH; see console/access.py
urlpatterns = [
    path('<str:secret>/session/', views.SessionView.as_view()),
    path('<str:secret>/overview/', views.OverviewView.as_view()),
    path('<str:secret>/organizers/', views.OrganizerListView.as_view()),
    path('<str:secret>/organizers/<int:user_id>/', views.OrganizerDetailView.as_view()),
    path('<str:secret>/organizers/<int:user_id>/role/', views.OrganizerRoleView.as_view()),
    path('<str:secret>/events/<str:event_id>/status/', views.EventStatusView.as_view()),
    path('<str:secret>/payments/', views.PaymentListView.as_view()),
    path('<str:secret>/payments/<uuid:payment_id>/', views.PaymentDetailView.as_view()),
    path('<str:secret>/payments/<uuid:payment_id>/resolve-refund/', views.ResolveRefundView.as_view()),
    path('<str:secret>/bookings/', views.BookingListView.as_view()),
    path('<str:secret>/actions/', views.ActionLogView.as_view()),
]
