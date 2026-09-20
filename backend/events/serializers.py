# backend/events/serializers.py
from rest_framework import serializers
from .models import Event

class EventSerializer(serializers.ModelSerializer):
    # Make organizer read-only so it cannot be spoofed in the API request
    organizer = serializers.ReadOnlyField(source='organizer.email')

    class Meta:
        model = Event
        fields = [
            'id', 'organizer', 'title', 'description', 
            'location', 'start_time', 'end_time', 
            'status', 'created_at', 'updated_at'
        ]
        
    def validate(self, data):
        """Ensure start time is before end time."""
        if data.get('start_time') and data.get('end_time'):
            if data['start_time'] >= data['end_time']:
                raise serializers.ValidationError("End time must occur after start time.")
        return data

# Append to backend/events/serializers.py
from django.contrib.auth.hashers import make_password
from .models import User, TicketTier

class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})

    class Meta:
        model = User
        fields = ['email', 'password', 'first_name', 'last_name', 'role']

    def create(self, validated_data):
        # We use create_user to ensure the password is encrypted in the database
        user = User.objects.create_user(
            email=validated_data['email'],
            username=validated_data['email'], # Fallback for Django's underlying system
            password=validated_data['password'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            role=validated_data.get('role', 'ATTENDEE')
        )
        return user

class TicketTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketTier
        fields = [
            'id', 'event', 'name', 'price', 
            'total_quantity', 'available_quantity', 
            'sale_start', 'sale_end'
        ]