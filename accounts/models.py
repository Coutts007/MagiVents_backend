from django.db import models

# Create your models here.
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    ROLE_CHOICES = (
        ('patron', 'Attendee'),
        ('curator', 'Organizer'),
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

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    @property
    def effective_avatar_url(self):
        if self.avatar:
            return self.avatar.url
        # Empty means "no photo": the frontend shows the user's initials
        return self.avatar_url or ''