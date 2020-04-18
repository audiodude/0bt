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
1. Then run certbot to install letsencrypt certs for your server.
1. Make sure your firewall settings allow the following: 443/tcp, 51413/tcp, 51413/udp, 5555/tcp, 5555/udp
1. Next install jinja2, either globally or in a virtual env:
   `$ pip3 install jinja2`
   or
   `$ python3 -m venv vevn && source venv/bin/activate && pip install jinja2`
1. Run the install script as root: `$ sudo python3 install.py`
1. Restart nginx and start docker-compose: `$ sudo service nginx restart` and `$ docker-compose up -d`
1. Stop the transmission client `$ docker-compose stop transmission`
1. Edit /opt/transmission/config/settings.json and set `"port-forwarding-enabled"` to `false`.
1. Restart the transmission client `$ docker-compose start transmission`
