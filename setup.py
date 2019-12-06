#!/usr/bin/env python3

from setuptools import setup, find_packages
try:  # pip version >= 10.0
    from pip._internal.req import parse_requirements
    from pip._internal.download import PipSession
except ImportError:  # pip version < 10.0
    from pip.req import parse_requirements
    from pip.download import PipSession

with open('README.rst') as readme_file:
    readme = readme_file.read()

with open('HISTORY.rst') as history_file:
    history = history_file.read()

install_reqs = parse_requirements('requirements.txt', session=PipSession())
reqs = [str(ir.req) for ir in install_reqs]

setup(
    name='nash-makerbot',
    python_requires='>=3.6',
    version='0.1.2',
    description="A crypto market maker bot that is easy understand and customize",
    long_description=readme + '\n\n' + history,
    author="Nash",
    author_email='contact@nash.io',
    url='https://gitlab.com/nash-io-public/nash-makerbot',
    packages=find_packages(include=['makerbot']),
    include_package_data=True,
    install_requires=reqs,
    entry_points = {
        'console_scripts': [
            'makerbot=makerbot.core:main'
        ]
    },
    license="MIT license",
    zip_safe=False,
    keywords='nash, ethereum, neo, hft, bot, trading, market, maker',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
    ]
)
