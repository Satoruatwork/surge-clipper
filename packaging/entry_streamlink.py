"""PyInstaller entry point for streamlink.exe: the live-stream grabber Surge shells out to, so the installed app
needs no Python on the PC. tools.streamlink() finds it next to Surge.exe."""
from streamlink_cli.main import main

main()
