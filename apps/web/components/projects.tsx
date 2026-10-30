"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { useUnsaved } from "@/lib/unsaved";
import { ProjectRuns } from "./project-runs";
import { api, allPages, jsonPost, type components } from "@/lib/client-api";
type Project = components["schemas"]["Project"];
export function Projects() {
  const [projects, setProjects] = useState<Project[]>([]), [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [selectedProject, setSelectedProject] = useState("");
  useEffect(() => { const controller = new AbortController(); allPages<Project>("projects?limit=100", { signal: controller.signal }).then(data => { if (!controller.signal.aborted) { setLoaded(true); setProjects(data.items); setSelectedProject(data.items[0]?.id || ""); } }).catch(e => { if (!controller.signal.aborted) setError(e.message); }); return () => controller.abort(); }, []);
  return <main className="workspace"><nav className="evidence-navigation"><Link href="/projects/new">New project</Link><Link href="/regressions">Regression library</Link></nav><div className="page-heading"><div><h1>Your visual test projects</h1><p>Give each test a source image, a human count and reviewed instance masks.</p></div><span className="workspace-label">Owner workspace</span></div>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="project-layout"><section><h2>Projects</h2>{projects.length ? <ul className="project-list">{projects.map(project => <li key={project.id}><Link href={"/projects/" + project.id}><h3>{project.name}</h3><p>{project.description || "Open seed setup and target configuration."}</p><span>Open project</span></Link></li>)}</ul> : loaded ? <div className="empty-state"><h3>No projects yet</h3><p>Create a project to prepare your first counting test.</p></div> : !error ? <p role="status">Loading projects…</p> : <p>Projects could not be loaded. Reload this page after checking the API service.</p>}</section>
    <NewProjectForm /></div>{projects.length > 0 && <><label>Project for latest runs<select value={selectedProject} onChange={e => setSelectedProject(e.target.value)}>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>{selectedProject && <ProjectRuns key={selectedProject} projectId={selectedProject} />}</>}</main>;
}

export function NewProjectForm({ acknowledgement = false }: { acknowledgement?: boolean }) {
  const router = useRouter();
  const { setDirty } = useUnsaved();
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      const project = await api<Project>("projects", jsonPost({ name: data.get("name"), description: data.get("description") }));
      setDirty(false); router.push("/projects/" + project.id);
    } catch (e) { setError(e instanceof Error ? e.message : "Project could not be created."); setBusy(false); }
  }
  return <form onSubmit={create} onChange={() => setDirty(true)} className="form-panel"><h2>Create a project</h2>
    {error && <p className="error" role="alert">{error}</p>}
    <label>Project name<input name="name" required minLength={3} maxLength={160} placeholder="Warehouse container counts" disabled={busy} /></label><label>Description<textarea name="description" maxLength={2000} rows={3} placeholder="What should this test protect?" disabled={busy} /></label>
    <label>Task type<input value="Object counting" readOnly /></label>
    {acknowledgement && <label className="checkbox"><input type="checkbox" required />I will use rights-cleared images and authorized targets for benign counting tests.</label>}
    <button className="primary" disabled={busy}>{busy ? "Creating…" : "Create project"}</button></form>;
}
export function NewProject() { return <main className="workspace narrow"><Link href="/projects">Projects</Link><h1>New visual test project</h1><NewProjectForm acknowledgement /></main>; }
