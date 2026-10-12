#!/bin/sh
set -eu
umask 077
exec python -m backend.app.deployment.service
