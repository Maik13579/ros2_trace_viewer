"""Standard node services, matched by exact suffix under any namespace.

Trace initialization has no interface type names. This is a name-based filter;
custom services using one of these exact suffixes are excluded as well.
"""

DEFAULT_SERVICE_SUFFIXES = (
    '/describe_parameters', '/get_parameter_types', '/get_parameters',
    '/list_parameters', '/set_parameters', '/set_parameters_atomically',
    '/get_type_description',
)


def is_default_service(name):
    return bool(name and name.endswith(DEFAULT_SERVICE_SUFFIXES))


def default_service_predicate(column):
    """Return a parameterized SQLite predicate for an internal column name."""
    return ('(' + ' OR '.join(f'{column} GLOB ?' for _ in DEFAULT_SERVICE_SUFFIXES) + ')',
            ['*' + suffix for suffix in DEFAULT_SERVICE_SUFFIXES])
