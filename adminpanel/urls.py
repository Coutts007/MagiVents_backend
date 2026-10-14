from django.urls import path

from . import views

# Every route needs a signed-in admin; everyone else gets 403 (see accounts.permissions.IsPlatformAdmin)
urlpatterns = [
    path('overview/', views.OverviewView.as_view()),
    path('organizers/', views.OrganizerListView.as_view()),
    path('organizers/<int:user_id>/', views.OrganizerDetailView.as_view()),
    path('organizers/<int:user_id>/resend-invite/', views.ResendInviteView.as_view()),
    path('organizers/<int:user_id>/payment-details/', views.OrganizerPaymentDetailsView.as_view()),
    path('organizers/<int:user_id>/suspend/', views.SuspendOrganizerView.as_view()),
    path('organizers/<int:user_id>/reactivate/', views.ReactivateOrganizerView.as_view()),
    path('organizers/<int:user_id>/payouts/', views.OrganizerPayoutsView.as_view()),
    path('category-fees/', views.CategoryFeesView.as_view()),
    path('platform-payment/', views.PlatformPaymentView.as_view()),
    path('listing-fees/', views.ListingFeeListView.as_view()),
    path('listing-fees/<str:event_id>/waive/', views.WaiveListingFeeView.as_view()),
    path('listing-fees/<str:event_id>/mark-paid/', views.MarkListingFeePaidView.as_view()),
]
