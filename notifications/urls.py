from django.urls import path

from . import views

urlpatterns = [
    path('', views.NotificationListView.as_view(), name='notifications'),
    path('read/', views.NotificationReadView.as_view(), name='notifications_read'),
]
