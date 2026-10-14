import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger('django.request')


def api_exception_handler(exc, context):
    """DRF's handler, plus: an unexpected exception becomes {"error": "Server error."} with HTTP 500 instead of a
    crash page (or, with DEBUG on, a traceback). The details are logged for staff."""
    response = exception_handler(exc, context)
    if response is None:
        view = context.get('view')
        logger.exception('Unhandled error in %s', view.__class__.__name__ if view else 'API view', exc_info=exc)
        return Response({'error': 'Server error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return response
