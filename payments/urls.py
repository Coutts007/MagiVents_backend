from django.urls import path

from . import hooks

# Paths avoid words Daraja rejects in callback URLs (M-Pesa, Safaricom, query, exec, sql, ...)
urlpatterns = [
    path('hooks/stk/<str:secret>/', hooks.stk_callback, name='hook_stk'),
    path('hooks/c2b-validate/<str:secret>/', hooks.c2b_validate, name='hook_c2b_validate'),
    path('hooks/c2b-confirm/<str:secret>/', hooks.c2b_confirm, name='hook_c2b_confirm'),
    path('hooks/paystack/', hooks.paystack_webhook, name='hook_paystack'),
]
