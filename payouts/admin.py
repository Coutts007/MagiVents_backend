from django.contrib import admin

from .models import PayoutAccount


@admin.register(PayoutAccount)
class PayoutAccountAdmin(admin.ModelAdmin):
    list_display = ['organizer', 'gathering', 'method', 'updated_at']
    list_filter = ['method']
    search_fields = ['organizer__email', 'gathering__title']
