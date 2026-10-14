from django.urls import path

from . import views

urlpatterns = [
    path('details/', views.MyPayoutDetailsView.as_view(), name='payout_details'),
    path('events/<str:gathering_id>/details/', views.EventPayoutDetailsView.as_view(), name='event_payout_details'),
]
