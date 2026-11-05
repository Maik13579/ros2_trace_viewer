from setuptools import find_packages, setup

setup(
    name="ros2_trace_viewer",
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/ros2_trace_viewer"]),
        ("share/ros2_trace_viewer", ["package.xml", "README.md"]),
    ],
    package_data={"ros2_trace_viewer": ["static/*"]},
    install_requires=["setuptools"],
    zip_safe=False,
    maintainer="Maik Knof",
    maintainer_email="maik.knof@gmx.de",
    description="Offline ROS 2 LTTng callback trace viewer",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "ros2_trace_viewer = ros2_trace_viewer.cli:main",
            "ros2-trace-viewer = ros2_trace_viewer.cli:main",
        ]
    },
)
