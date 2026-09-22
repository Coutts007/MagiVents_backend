from rest_framework.pagination import PageNumberPagination

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size' # Allows the frontend to append ?page_size=20
    max_page_size = 100 # Prevents malicious requests from requesting 1,000,000 items