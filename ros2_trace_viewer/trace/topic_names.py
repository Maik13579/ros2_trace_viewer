"""Names of standard parameter event topics, including namespaced recordings."""
PARAMETER_EVENTS_SUFFIX = '/parameter_events'


def is_parameter_events(name):
    return bool(name and name.endswith(PARAMETER_EVENTS_SUFFIX))


def parameter_events_predicate(column):
    """Exact suffix match; similar application topic names remain selectable."""
    return f'{column} GLOB ?', ['*'+PARAMETER_EVENTS_SUFFIX]
