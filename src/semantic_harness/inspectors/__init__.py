# -*- coding: utf-8 -*-
"""只读来源检查器。"""

from .document import inspect_document_elements
from .tabular import inspect_tabular_path
from .uploads import UploadSourceInspector

__all__ = [
    "UploadSourceInspector",
    "inspect_document_elements",
    "inspect_tabular_path",
]
