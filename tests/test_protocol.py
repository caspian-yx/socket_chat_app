from shared.protocol import MsgType, encode_msg, decode_msg


def test_encode_decode_roundtrip():
    msg = {
        "id": "1",
        "type": "request",
        "timestamp": 1,
        "command": MsgType.AUTH_LOGIN.value,
        "headers": {"version": "1.0"},
        "payload": {"username": "alice", "password": "secret"},
    }
    encoded = encode_msg(msg)
    decoded = decode_msg(encoded)
    assert decoded == msg
