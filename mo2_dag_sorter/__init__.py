"""MO2-DAG-Sorter - deterministic left-pane sorting for Mod Organizer 2.

MO2 imports this package and calls ``createPlugins()``.  Everything that needs
``mobase`` or PyQt lives in ``plugin``, so the pipeline can be imported and
tested outside MO2.
"""


def createPlugins():
    from .plugin import create_plugins
    return create_plugins()
