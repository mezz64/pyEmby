# Copyright (c) 2017-2026 John Mihalic <https://github.com/mezz64>
# Licensed under the MIT license.
#
# Releases are published to PyPI by .github/workflows/publish.yml when a tag
# matching the version below is pushed:
#
#   git tag 1.11 -m "1.11 release"
#   git push origin 1.11

import re
from pathlib import Path

from setuptools import setup

HERE = Path(__file__).parent

long_description = (HERE / 'README.md').read_text(encoding='utf-8')

# Single source of truth for the version lives in pyemby/constants.py.
VERSION = re.search(
    r"^__version__ = ['\"]([^'\"]+)['\"]",
    (HERE / 'pyemby' / 'constants.py').read_text(encoding='utf-8'),
    re.MULTILINE,
).group(1)

setup(
    name='pyEmby',
    packages=['pyemby'],
    version=VERSION,
    description='Python asyncio interface for the Emby Media Server API - live session tracking over websockets and playback control.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='John Mihalic',
    author_email='mezz64@users.noreply.github.com',
    license='MIT',
    url='https://github.com/mezz64/pyemby',
    download_url='https://github.com/mezz64/pyemby/tarball/{}'.format(VERSION),
    keywords=['emby', 'media server', 'api wrapper', 'asyncio', 'websocket', 'homeassistant'],
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Framework :: AsyncIO',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Topic :: Home Automation',
        'Topic :: Multimedia :: Video',
    ],
    python_requires='>=3.10',
    install_requires=[
        'aiohttp>=3.0',
        'async_timeout>=3.0',
    ],
)
