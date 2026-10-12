from django.contrib import admin

from .models import ConsoleAction


@admin.register(ConsoleAction)
class ConsoleActionAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'staff', 'action', 'target_type', 'target_label']
    list_filter = ['action', 'target_type']
    readonly_fields = [f.name for f in ConsoleAction._meta.fields]
