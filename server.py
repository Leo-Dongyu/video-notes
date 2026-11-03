"""兼容原有命令行入口；实现位于 video_notes 包。"""
import sys
from video_notes import server as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
