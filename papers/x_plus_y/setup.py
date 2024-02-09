from setuptools import find_packages, setup

setup(
    name="X_plus_Y",
    version="0.0",
    description="Papers",
    zip_safe=True,
    packages=find_packages(),
    python_requires=">=3.5",
    install_requires=[
        "cobaya>=3.0",
        "sacc>=0.4.2",
        #"pyccl",
        "numpy",
        "scipy"
    ],
    package_data={"xyshear": ["xyshear.yaml"]},
)
