"""Automatic routing of protobuf messages into and out of the ``Smart`` envelope.

Every protobuf the watch understands is nested two levels deep::

    Smart -> DeviceStatusService -> RemoteDeviceBatteryStatusRequest

Building that nesting by hand — and unwrapping it again with a chain of
``HasField`` checks — is mechanical work the ``Smart`` descriptor already
describes. :class:`SmartRouter` walks the descriptor once and derives the full
routing map, so callers pass the innermost message and never mention ``Smart``
at all. Adding a service to the ``.proto`` files makes it routable with no
library change.
"""

from __future__ import annotations

from typing import Dict, Optional, Set, Tuple

from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.message import Message

from ..errors import UnroutableMessage
from ..protobuf import gdi_smart_proto_pb2

#: (service field on Smart, message field on that service)
_Path = Tuple[str, str]


def _is_repeated(descriptor: FieldDescriptor) -> bool:
    """Repeated-field test that works across protobuf runtimes.

    protobuf 7 dropped ``FieldDescriptor.label`` in favour of ``is_repeated``;
    this library supports 4.x and up, so probe for the new attribute first.
    """
    is_repeated = getattr(descriptor, "is_repeated", None)
    if is_repeated is not None:
        return bool(is_repeated)
    return descriptor.label == FieldDescriptor.LABEL_REPEATED  # pragma: no cover


class SmartRouter:
    """Wraps and unwraps service messages in the ``Smart`` envelope.

    The routing table is built once per process and shared; it is derived
    purely from the compiled descriptors, so it cannot drift out of sync with
    the ``.proto`` files the way a hand-maintained table would.
    """

    _paths: Optional[Dict[str, _Path]] = None
    _ambiguous: Set[str] = set()

    # ── table construction ──────────────────────────────────────────────────

    @classmethod
    def _table(cls) -> Dict[str, _Path]:
        if cls._paths is None:
            paths: Dict[str, _Path] = {}
            ambiguous: Set[str] = set()

            for service_field in gdi_smart_proto_pb2.Smart.DESCRIPTOR.fields:
                if service_field.type != FieldDescriptor.TYPE_MESSAGE:
                    continue
                for msg_field in service_field.message_type.fields:
                    if msg_field.type != FieldDescriptor.TYPE_MESSAGE:
                        continue
                    if _is_repeated(msg_field):
                        continue
                    key = msg_field.message_type.full_name
                    if key in paths:
                        # The same message type hangs off two services; we
                        # cannot pick one for the caller.
                        ambiguous.add(key)
                        continue
                    paths[key] = (service_field.name, msg_field.name)

            for key in ambiguous:
                paths.pop(key, None)

            cls._paths = paths
            cls._ambiguous = ambiguous
        return cls._paths

    @classmethod
    def routable(cls, message: Message) -> bool:
        """Whether :meth:`wrap` can place *message* without guessing."""
        return message.DESCRIPTOR.full_name in cls._table()

    # ── wrapping ────────────────────────────────────────────────────────────

    @classmethod
    def wrap(cls, message: Message) -> gdi_smart_proto_pb2.Smart:
        """Nest a service message inside a fresh ``Smart`` envelope.

        Passing a ``Smart`` through is a no-op, so callers who genuinely need to
        hand-build an envelope still can.
        """
        if isinstance(message, gdi_smart_proto_pb2.Smart):
            return message

        key = message.DESCRIPTOR.full_name
        path = cls._table().get(key)
        if path is None:
            if key in cls._ambiguous:
                raise UnroutableMessage(
                    f"{key} appears under more than one service in Smart; "
                    f"build the Smart envelope explicitly for this one"
                )
            raise UnroutableMessage(
                f"{key} is not a field of any service in Smart — it cannot be "
                f"sent on its own"
            )

        service_field, message_field = path
        smart = gdi_smart_proto_pb2.Smart()
        service = getattr(smart, service_field)
        target = getattr(service, message_field)
        target.CopyFrom(message)
        # An all-defaults message (a bare request) would otherwise stay unset.
        target.SetInParent()
        return smart

    # ── unwrapping ──────────────────────────────────────────────────────────

    @staticmethod
    def unwrap(smart: gdi_smart_proto_pb2.Smart) -> Optional[Message]:
        """Return the innermost service message set on *smart*.

        Descends exactly the two levels the envelope is defined to have. Returns
        ``None`` for an empty envelope, and the service message itself if the
        service carries only scalar fields.
        """
        service_fields = smart.ListFields()
        if not service_fields:
            return None

        _, service = service_fields[0]
        if not isinstance(service, Message):
            return None

        for descriptor, value in service.ListFields():
            if descriptor.type == FieldDescriptor.TYPE_MESSAGE and not _is_repeated(descriptor):
                return value
        return service

    @staticmethod
    def service_name(smart: gdi_smart_proto_pb2.Smart) -> Optional[str]:
        """Name of the ``Smart`` field that is set, for logging and tracing."""
        fields = smart.ListFields()
        return fields[0][0].name if fields else None


__all__ = ["SmartRouter"]
