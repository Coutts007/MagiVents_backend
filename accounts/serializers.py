from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from core.images import absolute_media_url, data_url_to_file, is_data_url
from .models import User


class UserProfileSerializer(serializers.ModelSerializer):
    """Matches the frontend `UserProfile` type (src/types/auth.ts)."""
    id = serializers.CharField(read_only=True)
    name = serializers.CharField(source='first_name', required=False, allow_blank=True, max_length=150)
    avatarUrl = serializers.CharField(required=False, allow_blank=True)
    joinedDate = serializers.SerializerMethodField()
    authProvider = serializers.CharField(source='auth_provider', read_only=True)

    class Meta:
        model = User
        fields = ['id', 'name', 'email', 'avatarUrl', 'bio', 'city', 'role', 'joinedDate', 'authProvider']
        read_only_fields = ['role']

    def get_joinedDate(self, obj):
        return obj.joined_date.strftime('%B %Y') if obj.joined_date else ''

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.avatar:
            data['avatarUrl'] = absolute_media_url(self.context.get('request'), instance.avatar)
        else:
            data['avatarUrl'] = instance.effective_avatar_url
        return data

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exclude(pk=getattr(self.instance, 'pk', None)).exists():
            raise serializers.ValidationError('This email is already registered to another account.')
        return value

    def update(self, instance, validated_data):
        avatar = validated_data.pop('avatarUrl', None)
        if avatar is not None and avatar != self.to_representation(instance)['avatarUrl']:
            if is_data_url(avatar):
                instance.avatar = data_url_to_file(avatar, 'avatar')
                instance.avatar_url = None
            else:
                instance.avatar = None
                instance.avatar_url = avatar or None
        return super().update(instance, validated_data)


class RegisterSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('An account with this email already exists. Please sign in instead.')
        return value

    def validate(self, attrs):
        validate_password(attrs['password'], User(email=attrs['email'], first_name=attrs['name']))
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(
            username=validated_data['email'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data['name'].strip(),
            role='patron',
            auth_provider='email',
        )


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class GoogleAuthSerializer(serializers.Serializer):
    credential = serializers.CharField(help_text='Google ID token (JWT) from Google Identity Services')


class PasswordChangeSerializer(serializers.Serializer):
    currentPassword = serializers.CharField(write_only=True)
    newPassword = serializers.CharField(write_only=True)

    def validate_currentPassword(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Your current password is incorrect.')
        return value

    def validate_newPassword(self, value):
        validate_password(value, self.context['request'].user)
        return value


class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()
