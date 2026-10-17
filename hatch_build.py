"""Bundle the built web UI (web/dist) into the wheel as sopops/static, when it has been built."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class WebBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        dist = Path(self.root) / "web" / "dist"
        if (dist / "index.html").is_file():
            build_data["force_include"][str(dist)] = "sopops/static"
