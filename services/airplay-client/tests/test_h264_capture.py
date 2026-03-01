"""Tests for H.264 capture and AU parser."""

from airplay_client.capture.h264_capture import H264AUParser, _has_nal_type

# H.264 NAL start code
SC = b"\x00\x00\x00\x01"


def _make_au(nal_types: list[int]) -> bytes:
    """Build a fake H.264 Access Unit with given NAL types.

    Each NAL is just: start_code + header_byte + 2 padding bytes.
    """
    data = b""
    for nt in nal_types:
        # nal_ref_idc=3 for IDR/SPS/PPS, 0 for AUD
        idc = 3 if nt in (5, 7, 8) else 0
        header = (idc << 5) | nt
        data += SC + bytes([header, 0x00, 0x00])
    return data


def _make_aud_au(nal_types: list[int]) -> bytes:
    """Build an AU starting with AU delimiter (type 9), then the given NALs."""
    return _make_au([9] + nal_types)


class TestHasNalType:
    def test_finds_idr(self):
        au = _make_au([9, 7, 8, 5])
        assert _has_nal_type(au, 5) is True

    def test_no_idr_in_p_frame(self):
        au = _make_au([9, 1])
        assert _has_nal_type(au, 5) is False

    def test_empty_data(self):
        assert _has_nal_type(b"", 5) is False

    def test_no_start_code(self):
        assert _has_nal_type(b"\x01\x02\x03", 5) is False

    def test_finds_sps(self):
        au = _make_au([7, 8, 5])
        assert _has_nal_type(au, 7) is True


class TestH264AUParser:
    def test_single_au(self):
        """Single AU should not be emitted (need the start of the next AU)."""
        parser = H264AUParser()
        au = _make_aud_au([7, 8, 5])
        result = parser.feed(au)
        assert len(result) == 0  # No second AUD yet

    def test_two_aus(self):
        """Two concatenated AUs should yield the first one."""
        parser = H264AUParser()
        au1 = _make_aud_au([7, 8, 5])  # keyframe
        au2 = _make_aud_au([1])  # P-frame
        result = parser.feed(au1 + au2)
        assert len(result) == 1
        data, is_kf = result[0]
        assert data == au1
        assert is_kf is True

    def test_three_aus(self):
        parser = H264AUParser()
        au1 = _make_aud_au([7, 8, 5])
        au2 = _make_aud_au([1])
        au3 = _make_aud_au([1])
        result = parser.feed(au1 + au2 + au3)
        assert len(result) == 2
        assert result[0][0] == au1
        assert result[0][1] is True  # keyframe
        assert result[1][0] == au2
        assert result[1][1] is False  # P-frame

    def test_incremental_feed(self):
        """Feed data incrementally, simulating pipe reads."""
        parser = H264AUParser()
        au1 = _make_aud_au([7, 8, 5])
        au2 = _make_aud_au([1])

        combined = au1 + au2
        mid = len(combined) // 2

        # Feed first half
        result = parser.feed(combined[:mid])
        # Might or might not find a complete AU depending on split point

        # Feed second half
        result2 = parser.feed(combined[mid:])

        # Total should be 1 complete AU
        total = len(result) + len(result2)
        assert total == 1

    def test_skips_junk_before_first_aud(self):
        """Junk bytes before the first AUD should be skipped."""
        parser = H264AUParser()
        junk = b"\xff\xfe\xfd\xfc"
        au1 = _make_aud_au([7, 8, 5])
        au2 = _make_aud_au([1])
        result = parser.feed(junk + au1 + au2)
        assert len(result) == 1
        assert result[0][0] == au1

    def test_keyframe_detection(self):
        parser = H264AUParser()
        idr_au = _make_aud_au([7, 8, 5])  # SPS + PPS + IDR
        p_au = _make_aud_au([1])  # non-IDR slice
        trailing = _make_aud_au([1])  # need trailing to emit p_au

        result = parser.feed(idr_au + p_au + trailing)
        assert len(result) == 2
        assert result[0][1] is True   # keyframe
        assert result[1][1] is False  # non-keyframe

    def test_empty_feed(self):
        parser = H264AUParser()
        result = parser.feed(b"")
        assert result == []
