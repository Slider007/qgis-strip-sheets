def classFactory(iface):
    from .plugin import StripSheetsPlugin
    return StripSheetsPlugin(iface)
