import os
import sys

from jinja2 import Template

print('Prerequisites')
print('=============')
print('1) You should have docker and nginx installed.')
print('2) You should have your desired server name ready (example.com).')
print('3) You should have already installed your letsencrypt certificates.')
confirm = input('-- Are you ready to install (y/N)? ')
if not (confirm.startswith('y') or confirm.startswith('Y')):
  print('Cancelled.')
  sys.exit(1)

# Quit if not running as root.
if os.geteuid() != 0:
  print('This script should be run as root, as it creates directories at the')
  print('root level of your filesystem and modifies global nginx config')
  sys.exit(1)

print()
server_name = input('URL of server (eg foo.com): ')
pw = input('Transmission password: ')

# Create the nginx config.
nginx_path = '/etc/nginx/sites-available/%s' % server_name
print(' - Creating nginx config at %s' % nginx_path)
with open('templates/nginx.conf.tmpl') as f:
  template = Template(f.read())
template.stream(server_name=server_name).dump(nginx_path, encoding='utf-8')

# Link the nginx config into sites-enabled.
print(' - Linking nginx config to sites-enabled')
try:
  os.symlink(nginx_path, '/etc/nginx/sites-enabled/%s' % server_name)
except FileExistsError:
  pass


# Create the necessary directories that are mapped to the docker images.
for d in ('/opt/data', '/opt/app/db', '/opt/app/config',
          '/opt/transmission/config', '/opt/transmission/watch'):
  print(' - Creating %s' % d)
  os.makedirs(d, exist_ok=True)

# Create a symlink for the path prefix for uploads.
print(' - Symlinking /opt/data/up to /opt/data')
try:
  os.symlink('/opt/data', '/opt/data/up')
except FileExistsError:
  pass

# Create a netrc file for transmission.
print(' - Creating transmission login config')
with open('/opt/app/config/transmission.netrc', 'w') as f:
  f.write('default login transmission password %s\n' % pw)

# Create a .env file for docker-compose.
print(' - Creating Docker env file for compose')
with open('.env', 'w') as f:
  f.write('TRANSMISSION_PW=%s\n' % pw)

print()
print('nginx config modified, you should now restart nginx. On Ubuntu this is')
print('$ sudo service nginx restart')
print()
print('When you are ready to start all services, use the following command:')
print('$ docker-compose up -d')
print()
print('Done.')
