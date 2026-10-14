from django.contrib import admin

# Register your models here.
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    model = User
    list_display = ['email', 'username', 'role', 'city', 'is_staff', 'is_suspended']
    list_filter = UserAdmin.list_filter + ('role', 'is_suspended')
    fieldsets = UserAdmin.fieldsets + (
        ('Additional Info', {'fields': ('avatar', 'avatar_url', 'bio', 'city', 'role', 'auth_provider', 'google_id')}),
        ('Suspension', {'fields': ('is_suspended', 'suspended_at', 'suspension_reason')}),
    )