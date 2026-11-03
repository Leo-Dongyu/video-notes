"""统一定位项目根目录，避免启动位置和源码位置影响资源查找。"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
