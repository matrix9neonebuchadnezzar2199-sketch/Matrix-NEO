import os
import tempfile

from app.utils.file_ops import replace_or_move_overwrite


def test_replace_or_move_overwrite_same_dir():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "mneo_abc.mp4")
        dst = os.path.join(d, "user_name.mp4")
        with open(src, "wb") as f:
            f.write(b"x" * 100)
        replace_or_move_overwrite(src, dst)
        assert os.path.exists(dst)
        assert not os.path.exists(src)
