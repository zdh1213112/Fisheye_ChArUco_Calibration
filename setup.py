from pathlib import Path

from setuptools import find_packages, setup


PROJECT_ROOT = Path(__file__).resolve().parent

def read_requirements(filename):
    """Read dependencies from a requirements file."""
    return (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines()

setup(
    name='fisheye_3d_reconstruction',
    version='0.1.0',
    author='Jamie Milsom',
    author_email='jamieamilsom@gmail.com',
    description='A tool for calibrating cameras and performing 3D reconstruction with ultra-wide fisheye cameras',
    long_description=(PROJECT_ROOT / 'README.md').read_text(encoding='utf-8'),
    long_description_content_type='text/markdown',
    url='https://github.com/jamiemilsom/fisheye_3d_reconstruction',
    license='MIT',
    packages=find_packages(where='src'), 
    package_dir={'': 'src'},
    include_package_data=True,
    install_requires=read_requirements('requirements.txt'),
    extras_require={
        'dev': [
            'pytest'
        ],
    },
    classifiers=[
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'License :: OSI Approved :: MIT License',
        'Operating System :: Linux',
        'Operating System :: Microsoft :: Windows',
    ],
    python_requires='>=3.10',
)
