"""
pyemby.helpers
~~~~~~~~~~~~~~~~~~~~
Function helpers.
Copyright (c) 2017 John Mihalic <https://github.com/mezz64>
Licensed under the MIT license.
"""


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
