from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['reference', 'purpose', 'provider', 'amount', 'status', 'needs_refund', 'refund_resolved_at',
                    'receipt_number', 'created_at']
    list_filter = ['purpose', 'provider', 'status', 'needs_refund']
    search_fields = ['reference', 'provider_reference', 'receipt_number', 'booking__attendee_email']
    readonly_fields = [f.name for f in Payment._meta.fields] + ['raw_payloads']
