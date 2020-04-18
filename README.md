# 0bt (Zero Bit Torrent)
A fork of [0x0](https://github.com/mia-0/0x0) that provides torrent magnet links
and seeding abilities.

# Installation instructions
This software is meant to be run in a docker container alongside additional software (a bittorrent
client and a bittorrent tracker). All of the necessary configuration is contained within the
`docker-compose.yaml` file.

I have provided an install script that creates the necessary nginx config for the server, as well
as creates the necessary file system paths that the docker compose file maps to paths within the
containers. You will need python 3 and jinja2 to run the install script. If you are comfortable 
running the install steps manually you can forgo the script altogether.

To run the install script:

1. First install docker and nginx.
2. Next install jinja2, either globally or in a virtual env:
   `$ pip3 install jinja2`
   or
   `$ python3 -m venv vevn && source venv/bin/activate && pip install jinja2`
3. Finally run the install script as root: `$ sudo python3 install.py`
