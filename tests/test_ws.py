import pytest

from sdesk import ws


def test_the_accept_key_matches_the_example_in_the_standard():
    assert ws.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_an_upgrade_request_is_parsed_and_answered():
    req = (b"GET /tabs HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
           b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nOrigin: chrome-extension://abc\r\n\r\n")
    assert ws.parse_request(req[:40]) is None                         # not complete yet
    path, headers = ws.parse_request(req)
    assert path == "/tabs" and headers["origin"] == "chrome-extension://abc"
    resp = ws.handshake_response(headers)
    assert resp.startswith(b"HTTP/1.1 101") and b"s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" in resp


def test_a_plain_http_request_is_refused():
    _path, headers = ws.parse_request(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    with pytest.raises(ws.ProtocolError):
        ws.handshake_response(headers)
    with pytest.raises(ws.ProtocolError):
        ws.parse_request(b"POST / HTTP/1.1\r\n\r\n")


@pytest.mark.parametrize("size", [0, 5, 125, 126, 300, 65535, 65536, 70000])
def test_messages_of_every_size_survive_the_round_trip(size):
    text = ("é" * size)[:size]
    out = ws.Decoder().feed(ws.client_frame(text))
    assert out == [(ws.OP_TEXT, text.encode())]


def test_server_frames_are_unmasked_and_have_the_right_length_header():
    assert ws.encode_frame("hi") == b"\x81\x02hi"
    assert ws.encode_frame(b"x" * 200)[:4] == b"\x81\x7e\x00\xc8"


def test_bytes_arriving_in_pieces_and_several_frames_in_one_read():
    d = ws.Decoder()
    frame = ws.client_frame("hello") + ws.client_frame("world")
    assert d.feed(frame[:3]) == []
    assert d.feed(frame[3:9]) == []                                  # the first frame is 11 bytes long: still incomplete
    assert d.feed(frame[9:]) == [(ws.OP_TEXT, b"hello"), (ws.OP_TEXT, b"world")]


def test_fragmented_messages_are_joined_and_pings_may_come_in_between():
    d = ws.Decoder()
    data = (ws.client_frame("Hel", fin=False) + ws.client_frame(b"", ws.OP_PING)
            + ws.client_frame("lo ", ws.OP_CONT, fin=False) + ws.client_frame("you", ws.OP_CONT))
    assert d.feed(data) == [(ws.OP_PING, b""), (ws.OP_TEXT, b"Hello you")]


def test_unmasked_client_frames_and_stray_continuations_are_protocol_errors():
    with pytest.raises(ws.ProtocolError):
        ws.Decoder().feed(b"\x81\x02hi")
    with pytest.raises(ws.ProtocolError):
        ws.Decoder().feed(ws.client_frame("x", ws.OP_CONT))


def test_oversized_frames_are_refused_before_they_are_read():
    with pytest.raises(ws.ProtocolError):
        ws.Decoder().feed(b"\x81\xff" + (ws.MAX_MESSAGE + 1).to_bytes(8, "big") + b"\0\0\0\0")
