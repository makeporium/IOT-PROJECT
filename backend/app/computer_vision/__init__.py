"""Computer Vision package for DOORSAFE."""
from .detector import YOLODetector
from .annotator import FrameAnnotator

__all__ = ["YOLODetector", "FrameAnnotator"]
