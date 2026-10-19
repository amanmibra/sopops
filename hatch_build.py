"""Bundle the built web UI (web/dist) into the wheel as sopops_server/static, when it has been built, and the onboarding
skill (skills/sopops-onboard/SKILL.md) as sopops/onboarding/SKILL.md, so `get_kickoff` matches the installed version."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class WebBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        dist = Path(self.root) / "web" / "dist"
        if (dist / "index.html").is_file():
            build_data["force_include"][str(dist)] = "sopops_server/static"
        skill = Path(self.root) / "skills" / "sopops-onboard" / "SKILL.md"
        build_data["force_include"][str(skill)] = "sopops/onboarding/SKILL.md"
