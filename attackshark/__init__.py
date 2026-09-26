"""Open-source driver for the Attack Shark X3 wireless mouse."""
from .device import AttackSharkX3, DeviceNotFound
from . import protocol

__all__ = ["AttackSharkX3", "DeviceNotFound", "protocol"]
__version__ = "1.2.0"
