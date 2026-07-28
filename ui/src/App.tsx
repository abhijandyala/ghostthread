import { useState } from "react";
import type { Tab, Project } from "./data/types";
import {
  loadProjects,
  persistProjects,
  loadActiveProjectName,
  persistActiveProjectName,
  clearActiveProjectName,
} from "./data/project";
import { ProjectContext } from "./ProjectContext";
import { PipelineDataProvider } from "./data/store";
import Sidebar from "./components/Sidebar";
import TopBar from "./components/TopBar";
import SignOutTransition from "./components/SignOutTransition";
import DashboardTab from "./components/DashboardTab";
import InboxTab from "./components/InboxTab";
import ApprovalsTab from "./components/ApprovalsTab";
import MemoryTab from "./components/MemoryTab";
import SettingsTab from "./components/SettingsTab";
import CreateProject from "./components/CreateProject";

const tabs: Record<Tab, React.ComponentType> = {
  dashboard: DashboardTab,
  inbox: InboxTab,
  approvals: ApprovalsTab,
  memory: MemoryTab,
  settings: SettingsTab,
};

export default function App() {
  const [projects, setProjects] = useState<Project[]>(() => loadProjects());
  const [activeName, setActiveName] = useState<string | null>(() => loadActiveProjectName());
  const [creating, setCreating] = useState(false);
  const [activeTab, setActiveTab] = useState<Tab>("dashboard");
  const [signingOut, setSigningOut] = useState(false);

  const activeProject = projects.find((p) => p.name === activeName) ?? null;

  const handleProjectCreated = (name: string, connectors: string[]) => {
    const project: Project = { name, connectors, createdAt: new Date().toISOString() };
    const next = [project, ...projects.filter((p) => p.name !== name)];
    setProjects(next);
    persistProjects(next);
    setActiveName(name);
    persistActiveProjectName(name);
    setCreating(false);
    setActiveTab("dashboard");
  };

  const handleSelectProject = (name: string) => {
    setActiveName(name);
    persistActiveProjectName(name);
    setActiveTab("dashboard");
  };

  // Fires while the palette wipe fully covers the screen: swap to onboarding
  // behind the layers, so the reveal cascade uncovers the fresh start.
  const handleSignOutCovered = () => {
    setActiveName(null);
    clearActiveProjectName();
    setCreating(false);
    setActiveTab("dashboard");
  };

  const ActiveComponent = tabs[activeTab];

  return (
    <>
      {creating || !activeProject ? (
        <CreateProject
          onProjectCreated={handleProjectCreated}
          onCancel={activeProject ? () => setCreating(false) : undefined}
        />
      ) : (
        <ProjectContext.Provider value={activeProject}>
          <PipelineDataProvider>
            <div className="flex h-screen bg-bg overflow-hidden animate-scale-in">
              <Sidebar
                activeTab={activeTab}
                onTabChange={setActiveTab}
                projects={projects}
                activeProject={activeProject}
                onSelectProject={handleSelectProject}
                onNewProject={() => setCreating(true)}
                onSignOut={() => setSigningOut(true)}
              />
              <div className="flex-1 flex flex-col min-w-0">
                <TopBar activeTab={activeTab} />
                <main className="flex-1 overflow-y-auto">
                  <ActiveComponent />
                </main>
              </div>
            </div>
          </PipelineDataProvider>
        </ProjectContext.Provider>
      )}

      {signingOut && (
        <SignOutTransition
          name="Abhi"
          onCovered={handleSignOutCovered}
          onDone={() => setSigningOut(false)}
        />
      )}
    </>
  );
}
