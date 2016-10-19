"""
pyemby.constants
~~~~~~~~~~~~~~~~~~~~
Constants list
Copyright (c) 2016 John Mihalic <https://github.com/mezz64>
Licensed under the MIT license.
"""

MAJOR_VERSION = 0
MINOR_VERSION = 1
__version__ = '{}.{}'.format(MAJOR_VERSION, MINOR_VERSION)

DEFAULT_HEADERS = {
    'Content-Type': "application/json",
    'Accept': "application/json"
}
