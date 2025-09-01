#!/usr/bin/env python3
"""
Setup script for IRS 990 Tools.
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="irs990tools",
    version="0.1.0",
    author="Data Republican",
    description="Tools for processing IRS 990 tax filings",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    py_modules=["irs990tools", "config", "xpaths"],
    entry_points={
        "console_scripts": [
            "irs990tools=irs990tools:main",
        ],
    },
    install_requires=[
        "requests",
        "beautifulsoup4",
        "lxml",
        "pandas",
        "tqdm",
        "mako",
        "psutil",
    ],
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.7",
    keywords="irs 990 tax charity nonprofit data processing",
)