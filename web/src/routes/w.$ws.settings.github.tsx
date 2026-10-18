import { createFileRoute } from "@tanstack/react-router";
import { RepoCard } from "@/components/repo";
import { SectionHead } from "@/components/settings";

export const Route = createFileRoute("/w/$ws/settings/github")({ component: GitHub });

function GitHub() {
  return (
    <>
      <SectionHead title="Keep your instructions in a GitHub repository" />
      <RepoCard />
    </>
  );
}
