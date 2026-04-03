import os
import tempfile

from app.services.download_service import _replace_or_move_overwrite


def test_replace_or_move():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "mneo_abc.mp4")
        dst = os.path.join(d, "user_name.mp4")
        with open(src, "wb") as f:
            f.write(b"x" * 100)
        _replace_or_move_overwrite(src, dst)
        assert os.path.exists(dst)
        assert not os.path.exists(src)
