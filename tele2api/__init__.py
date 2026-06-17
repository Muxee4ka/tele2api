"""tele2api — клиент API маркета t2 (бывш. Tele2)."""

from .tele2_api import (
    Tele2Api,
    Tele2ApiError,
    Tele2AuthError,
    Tele2LotError,
    Tele2ServiceError,
    Service,
)

__all__ = [
    'Tele2Api',
    'Tele2ApiError',
    'Tele2AuthError',
    'Tele2LotError',
    'Tele2ServiceError',
    'Service',
]
__version__ = '3.0.0'
