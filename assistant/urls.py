from django.urls import path

from . import views

urlpatterns = [
    path('event/', views.EventAssistantView.as_view(), name='assistant_event'),
]
