import base64
import binascii
import re
import uuid

from django.core.files.base import ContentFile
from rest_framework import serializers

DATA_URL_RE = re.compile(r'^data:image/(?P<ext>[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$', re.DOTALL)

EXTENSIONS = {'jpeg': 'jpg', 'svg+xml': 'svg'}


def is_data_url(value):
    return isinstance(value, str) and value.startswith('data:image/')


def data_url_to_file(value, prefix):
    """Decode a base64 `data:image/...` URL (as produced by the frontend upload zone) into a ContentFile."""
    match = DATA_URL_RE.match(value)
    if not match:
        raise serializers.ValidationError('Unsupported image data.')
    try:
        content = base64.b64decode(match.group('data'), validate=True)
    except (binascii.Error, ValueError):
        raise serializers.ValidationError('Image data is not valid base64.')
    ext = match.group('ext').lower()
    ext = EXTENSIONS.get(ext, ext)
    return ContentFile(content, name=f'{prefix}-{uuid.uuid4().hex[:12]}.{ext}')


def absolute_media_url(request, file_field):
    url = file_field.url
    return request.build_absolute_uri(url) if request else url
