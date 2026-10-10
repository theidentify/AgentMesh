"""Project workflow.py is not the third-party 'workflow' distribution.

Override the third-party PyInstaller hook which requires distribution metadata
for an unrelated package. Our local module imports its own dependencies normally.
"""
