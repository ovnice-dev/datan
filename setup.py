# setup.py
from setuptools import setup, Extension
from Cython.Build import cythonize
import sys

ext = Extension(
    "engine.evaluate",                # IMPORTANT : nom du module = engine.evaluate
    sources=["engine/evaluate.pyx"],
)

setup(
    name="engine_evaluate",
    ext_modules=cythonize([ext], compiler_directives={"language_level": "3"}),
)
