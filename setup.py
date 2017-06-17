# Copyright (c) 2017 John Mihalic <https://github.com/mezz64>
# Licensed under the MIT license.

# Used this guide to create module
# http://peterdowns.com/posts/first-time-with-pypi.html

# git tag 0.1 -m "0.1 release"
# git push --tags origin master
#
# Upload to PyPI Live
# python setup.py register -r pypi
# python setup.py sdist upload -r pypi


from distutils.core import setup
setup(
    name='pyEmby',
    packages=['pyemby'],
    version='1.3',
    description='Provides a python interface to interact with a Emby media server.',
    author='John Mihalic',
    author_email='mezz64@users.noreply.github.com',
    url='https://github.com/mezz64/pyemby',
    download_url = 'https://github.com/mezz64/pyemby/tarball/1.3',
    keywords= ['emby', 'media sever', 'api wrapper'],
    classifiers = [],
    )
