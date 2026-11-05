"""Loaded by Python child processes only during explicitly offline tests."""
import ipaddress
import os
import sys


def loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def audit(event, args):
    if event == 'socket.getaddrinfo' and not loopback(args[0]):
        raise PermissionError('offline tests block external DNS')
    if event == 'socket.connect':
        address = args[1]
        # Unix sockets are local too. Internet sockets must use loopback.
        if isinstance(address, tuple) and not loopback(address[0]):
            raise PermissionError('offline tests block external connections')


if os.environ.get('HALLPANEL_OFFLINE_TESTS') == '1':
    sys.addaudithook(audit)
