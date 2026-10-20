"""Bundle the built web UI (web/dist) into the wheel as sopops_server/static, when it has been built, and the skills
(skills/<name>/SKILL.md) as sopops_server/onboarding/skills/<name>.md, so `get_kickoff` and `get_ab_test_guide` match the
installed version."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class WebBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        dist = Path(self.root) / "web" / "dist"
        if (dist / "index.html").is_file():
            build_data["force_include"][str(dist)] = "sopops_server/static"
        for skill in (Path(self.root) / "skills").glob("*/SKILL.md"):
            build_data["force_include"][str(skill)] = f"sopops_server/onboarding/skills/{skill.parent.name}.md"
