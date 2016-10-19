# Introduction

This is a python module aiming to interact with the Emby Media Server (http://emby.media) api.

Code is licensed under the MIT license.

Getting Started
===============

# Usage

```python
import pyemby.emby

Emby = pyemby.emby.EmbyRemote('api_key', 'http://192.168.1.5:8096')
```

# Methods

def unique_id(self):
"""Return unique ID for connection to Emby."""

def get_sessions_url(self):
"""Return the session url."""

def get_sessions(self):
"""Return active client sessions."""

def set_playstate(self, session, state):
"""Send media commands to client."""
       
def play(self, session):
"""Call play command."""

def pause(self, session):
"""Call pause command."""

def stop(self, session):
"""Call stop command."""

def next_track(self, session):
"""Call next track command."""

def previous_track(self, session):
"""Call previous track command."""

def get_image(self, item_id, style, played=0):
"""Return media image."""