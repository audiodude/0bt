#!/bin/bash
set -e

mkdir -p /opt/data
ln -s /opt/data /opt/data/up
mkdir -p /opt/app/db
mkdir -p /opt/app/config
mkdir -p /opt/transmission/config
mkdir -p /opt/transmission/watch

# Write the nginx config
# Write the config/transmission.netrc file
