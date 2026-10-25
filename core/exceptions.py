import logging
import math

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger('django.request')

SERVER_ERROR = 'Something went wrong on our side. Please try again in a moment.'

# DRF's built-in messages, said in a way that tells people what to do. Only replaced when the view did not write
# its own message, and never with anything about how the server works.
FRIENDLIER = {
    exceptions.NotAuthenticated: 'Please sign in to continue.',
    exceptions.AuthenticationFailed: 'Your session has ended. Please sign in again.',
    exceptions.PermissionDenied: 'You do not have access to this. If you think you should, contact us.',
    exceptions.NotFound: 'We could not find what you were looking for. It may have been removed.',
    exceptions.MethodNotAllowed: 'That action is not available here.',
    exceptions.UnsupportedMediaType: 'That type of file or data is not supported.',
    exceptions.ParseError: 'The information sent could not be read. Please try again.',
}

# Their text carries technical detail (the HTTP method, a JSON parser error, simplejwt's token checks), so it is
# always replaced. A subclass (such as simplejwt's InvalidToken) is replaced too; other views' own messages are kept.
ALWAYS_REPLACED = {exceptions.NotAuthenticated, exceptions.MethodNotAllowed, exceptions.UnsupportedMediaType,
                   exceptions.ParseError}


def wait_phrase(seconds):
    seconds = max(1, math.ceil(seconds or 1))
    if seconds < 60:
        return f'{seconds} second{"s" if seconds != 1 else ""}'
    minutes = math.ceil(seconds / 60)
    return f'{minutes} minute{"s" if minutes != 1 else ""}'


def api_exception_handler(exc, context):
    """DRF's handler, plus: an unexpected exception becomes a short, generic 500 message instead of a crash page
    (or, with DEBUG on, a traceback), and DRF's stock messages become friendlier. Details are logged for staff."""
    response = exception_handler(exc, context)
    if response is None:
        view = context.get('view')
        logger.exception('Unhandled error in %s', view.__class__.__name__ if view else 'API view', exc_info=exc)
        return Response({'error': SERVER_ERROR}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if isinstance(exc, exceptions.Throttled):
        response.data = {'error': f'Too many attempts. Please wait {wait_phrase(exc.wait)} and try again.',
                         'code': 'throttled'}
        return response
    for exc_class, message in FRIENDLIER.items():
        if isinstance(exc, exc_class) and (exc_class in ALWAYS_REPLACED or type(exc) is not exc_class
                                           or str(exc.detail) == str(exc_class.default_detail)):
            response.data = {'error': message, 'code': exc_class.default_code}
            break
    return response
