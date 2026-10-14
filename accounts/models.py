from django.db import models

# Create your models here.
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    ROLE_CHOICES = (
        ('patron', 'Attendee'),
        ('curator', 'Organizer'),
        # Platform administrators. Kept in sync with Django's is_staff; superusers are always admins.
        ('admin', 'Admin'),
    )
    PROVIDER_CHOICES = (
        ('email', 'Email & Password'),
        ('google', 'Google Identity'),
    )

    email = models.EmailField(unique=True)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    avatar_url = models.CharField(max_length=500, null=True, blank=True)
    bio = models.TextField(blank=True, default='')
    city = models.CharField(max_length=120, blank=True, default='')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='patron')
    auth_provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default='email')
    google_id = models.CharField(max_length=255, null=True, blank=True, unique=True)
    joined_date = models.DateField(auto_now_add=True)

    # A suspended organizer can still sign in and see their data, but cannot create or publish events
    is_suspended = models.BooleanField(default=False)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True, default='')

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remembered so save() knows whether the role or the staff flag was the one that changed
        self._loaded_role, self._loaded_is_staff = self.role, self.is_staff

    def save(self, *args, **kwargs):
        self.sync_admin_role()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and {'role', 'is_staff'} & set(update_fields):
            # Saving one of the pair saves both, so they cannot drift apart
            kwargs['update_fields'] = {*update_fields, 'role', 'is_staff'}
        super().save(*args, **kwargs)
        self._loaded_role, self._loaded_is_staff = self.role, self.is_staff

    def sync_admin_role(self):
        """role == 'admin' <=> is_staff. Whichever of the two was changed decides; superusers are always admins."""
        if self.is_superuser:
            self.role, self.is_staff = 'admin', True
        elif self.role != self._loaded_role or self._state.adding and self.role == 'admin':
            self.is_staff = self.role == 'admin'
        elif self.is_staff != self._loaded_is_staff or self.is_staff != (self.role == 'admin'):
            # Staff status ticked or unticked in the Django admin. A former admin who owns events stays an organizer.
            if self.is_staff:
                self.role = 'admin'
            elif self.role == 'admin':
                owns_events = self.pk is not None and self.organized_gatherings.exists()
                self.role = 'curator' if owns_events else 'patron'

    @property
    def is_platform_admin(self):
        return self.is_active and self.role == 'admin'

    @property
    def is_organizer(self):
        # Admins keep the organizer abilities, so staff who already ran events lose nothing
        return self.role in ('curator', 'admin')

    @property
    def effective_avatar_url(self):
        if self.avatar:
            return self.avatar.url
        # Empty means "no photo": the frontend shows the user's initials
        return self.avatar_url or ''
