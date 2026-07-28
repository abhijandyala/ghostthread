import { createContext, useContext } from "react";
import type { Project } from "./data/types";

export const ProjectContext = createContext<Project | null>(null);

export function useProject() {
  return useContext(ProjectContext);
}
