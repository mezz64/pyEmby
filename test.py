"""
Test program for emby api.
"""

import logging
import pyemby.emby as emby

logging.basicConfig(filename='api_out.log', level=logging.DEBUG, format='%(asctime)s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')


def main():
    """Main function"""
    media = emby.EmbyRemote('756f3df058ad410c96ee14844e76b001', 'http://192.168.11.5:8096')

    print(media.unique_id)
    print(media.get_sessions_url)
    print(media.get_sessions())
    print(media.get_image('123item', 'Thumb', 75))

main()
