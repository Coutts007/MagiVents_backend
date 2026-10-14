from django.contrib import admin

from .models import PayoutAccount


@admin.register(PayoutAccount)
class PayoutAccountAdmin(admin.ModelAdmin):
    list_display = ['organizer', 'gathering', 'method', 'updated_at']
    list_filter = ['method']
    search_fields = ['organizer__email', 'gathering__title']


from .models import Payout  # noqa: E402


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ['organizer', 'amount', 'reference', 'paid_on', 'recorded_by', 'created_at']
    search_fields = ['organizer__email', 'reference']
    readonly_fields = ['recorded_by', 'created_at']
