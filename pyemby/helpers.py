"""
pyemby.helpers
~~~~~~~~~~~~~~~~~~~~
Function helpers.
Copyright (c) 2017-2019 John Mihalic <https://github.com/mezz64>
Licensed under the MIT license.
"""
import collections.abc


def deprecated_name(name):
    """Allow old method names for backwards compatability. """
    def decorator(func):
        """Decorator function."""
        def func_wrapper(self):
            """Wrapper for original function."""
            if hasattr(self, name):
                # Return the old property
                return getattr(self, name)
            else:
                return func(self)
        return func_wrapper
    return decorator


def clean_none_dict_values(obj):
    """
    Recursively remove keys with a value of None
    """
    if not isinstance(obj, collections.abc.Iterable) or isinstance(obj, str):
        return obj

    queue = [obj]

    while queue:
        item = queue.pop()

        if isinstance(item, collections.abc.Mapping):
            mutable = isinstance(item, collections.abc.MutableMapping)
            remove = []

            for key, value in item.items():
                if value is None and mutable:
                    remove.append(key)

                elif isinstance(value, str):
                    continue

                elif isinstance(value, collections.abc.Iterable):
                    queue.append(value)

            if mutable:
                # Remove keys with None value
                for key in remove:
                    item.pop(key)

        elif isinstance(item, collections.abc.Iterable):
            for value in item:
                if value is None or isinstance(value, str):
                    continue
                elif isinstance(value, collections.abc.Iterable):
                    queue.append(value)

    return obj
